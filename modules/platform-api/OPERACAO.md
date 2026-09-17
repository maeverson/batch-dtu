# Platform API — Guia de operação

Referência de uso do pacote `platform_api` (`src/platform_api/`). `CLAUDE.md` dá as decisões e o
ciclo de mudança; `SPEC.md` dá requisitos e critérios de aceite. Este documento é "como rodar e
testar", na linha de `modules/job-catalog/OPERACAO.md`.

## Subir o ambiente

```bash
docker compose --profile auth up -d          # Keycloak (OIDC local)
docker compose --profile legacy up -d --build  # host legado (SSH + main.sh + wrapper)
```

O container `legacy` sobe e o caminho completo (`POST /executions` → SSH → `main.sh` → log no Loki)
foi verificado em 16/09/2026. Se ele falhar no seu ambiente, note que **a garantia de segurança do
backend SSH não depende do container estar de pé**: ela é verificada rodando o wrapper de verdade
via bash, sem docker — ver `tests/test_platform_api_ssh_integration.py` e a seção "Verificar sem o
container" abaixo.

Dois pontos do fixture que valem saber (ambos já corrigidos, mas explicam o formato do ambiente):

- A chave gerada em `docker/legacy/keys/` precisa pertencer ao **usuário do host**, porque quem a lê
  é a Platform API rodando fora do container. O `entrypoint.sh` faz `chown` para `KEYS_UID`/`KEYS_GID`
  (default `1000`) — se o seu `id -u` for outro, exporte-os (ver `.env.example`).
- Os contratos servidos são, por default, os **4 fixtures sintéticos** da imagem. Nenhum deles existe
  no catálogo, então executar um job real pela UI falha na pré-validação. Para exercitar jobs reais,
  aponte `LEGACY_PROCESSES_DIR` para o `framework/processes` de um pacote de coleta (ver
  `.env.example`) — conteúdo de cliente real, uso estritamente local.

```bash
set -a; . deploy/platform-api/local.env; set +a   # variáveis sobem com o serviço, não com a sessão
platform-api   # sobe uvicorn em :8000 — docs interativos em /docs
```

O processo **recusa subir** com configuração incompleta (`ConfigurationError`, mensagem sem stack
trace): sem `SSH_BACKEND_KNOWN_HOSTS` nem o escape explícito de desenvolvimento, sem
`PLATFORM_ENVIRONMENT`/`PLATFORM_HOST`, ou com `LOG_BACKEND=newrelic` sem chave de consulta. É de
propósito — uma API que sobe sem verificar chave de host é pior que uma que não sobe.

`GET /health` devolve `environment`, `host` e `log_backend`: com um deploy por ambiente, saber
**qual** instância respondeu é parte do diagnóstico.

## Escopo do deploy — uma instância, um ambiente, um host

`PLATFORM_ENVIRONMENT` (`UAT`/`PROD`/…) e `PLATFORM_HOST` (o hostname do catálogo:
`com-ins-bch-mdw-dtu-1`, `srv-sftp-2`) recortam tudo: listagem, execução e administração. Job fora
do recorte responde 403 ("não é servido por esta instância") **inclusive para `batch.admin`** — a
separação é do deploy, e é ela que garante que o canal SSH desta instância só alcança o host que
ela declara operar. `SSH_BACKEND_HOST` é o endereço de rede (IP) do mesmo host.

## Autenticação (Keycloak local)

Realm `batch-dtu`, roles `batch.viewer/operator/operator-prod/admin`, usuários de teste
`viewer/operator/operator-prod/admin-batch` (senha = usuário). Pegar um token:

```bash
curl -s -X POST http://localhost:8080/realms/batch-dtu/protocol/openid-connect/token \
  -d grant_type=password -d client_id=platform-api -d client_secret=platform-api-dev-secret \
  -d username=operator-prod -d password=operator-prod -d scope=openid \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])'
```

O token carrega `realm_access.roles` e `preferred_username`. **A role do token é a autorização
inteira** — não há mais `role_binding` consultado em runtime (decisão do cliente; ver
`CLAUDE.md`). As claims `batch_domains`/`batch_environments` da fixture do Keycloak continuam
ignoradas.

Trocar para Entra ID real é só variável (ver `deploy/platform-api/uat.env.example`):
`OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_JWKS_URI` (**obrigatório** no Entra — o default é derivado
no formato Keycloak), `OIDC_ROLES_CLAIM=roles`, `OIDC_SUBJECT_CLAIM=oid`,
`OIDC_DISPLAY_NAME_CLAIM=preferred_username`.

## Conceder acesso — no Entra ID

Grupo do cliente atribuído à app role, na *enterprise application*. É o único lugar; revogar é
tirar o grupo. Mantenha grupos separados para operador de UAT e de PROD — com uma instância por
ambiente, é essa separação que impede alguém de homologação disparar contra cliente real.

## Endpoints

| Rota | O que faz |
|---|---|
| `GET /jobs?domain=&client=&environment=&status=` | Lista jobs dentro do escopo do token |
| `GET /jobs/{id}` | Um job |
| `GET /jobs/{id}/schedules` | Agendas do job (`schedule_expr`, timezone, `raw_line`) |
| `GET /jobs/{id}/contract` | Contrato JSON da versão corrente |
| `GET /jobs/{id}/reconciliation` | Divergências abertas na reconciliação mais recente do host (Back Office, Etapa 1.4) |
| `POST /jobs/{id}/validate` | Roda o schema (`catalog.contract_schema`) contra o contrato corrente |
| `PATCH /jobs/{id}/status` | Grava o estado desejado + abre `crontab_change_request` — ver ciclo no `CLAUDE.md` |
| `POST /executions` | Execução manual / reprocesso — ver fluxo abaixo |
| `GET /executions?job_id=` / `GET /executions/{id}` | Consulta |
| `GET /executions/{id}/logs` | Log por `execution_id` — New Relic (NerdGraph) em ambiente implantado, Loki no compose local (`LOG_BACKEND`) |
| `GET /change-requests?state=&host=` | Worklist do Back Office |
| `POST /change-requests/{id}/cancel` | Desiste de uma mudança pendente |
| `GET /audit-events?...` | Exige `batch.admin` |
| `GET /me` | Subject, display_name, roles do token, `environment`/`host` da instância, `is_admin` |
| `GET /admin/jobs` … | CRUD de catálogo (`POST`/`PATCH`/`DELETE` + `PUT /admin/jobs/{id}/contract`) — exige `batch.admin` |

### `POST /executions` — **200 ao despachar, não ao terminar**

A resposta é o comprovante do despacho (`status: running`), não o desfecho: o SSH continua rodando
depois que o cliente já recebeu 200. Quem acompanha é o New Relic (evento `BatchExecution` + log
por `execution_id`); o desfecho cai em `GET /executions/{id}` quando o `main.sh` termina.

Consequência operacional que precisa de alerta, não de documentação: execução que trava fica em
`running` e **ninguém é avisado pela resposta HTTP**. O alerta de `running` há mais de 30 minutos
(`deploy/newrelic/README.md`) é o que substitui o exit code que a resposta síncrona dava.

### `POST /executions` — o que é obrigatório

- `confirm_target_dates: true` sempre.
- `confirm_upload_remote: true` **quando** o contrato tem `upload_remote`/`download_remote` **e**
  o job é PROD — confirmação reforçada real, não cosmética: sem ela, 422.
- Antes de executar de verdade, a API roda **pré-validação** (`--validate-file`) via o mesmo
  `ExecutionBackend` — se ela falhar, nada é persistido e a resposta é 422. Duas chamadas SSH por
  execução bem-sucedida (validação + execução), de propósito.
- `idempotency_key` opcional: reenvio da mesma chave devolve a execução já registrada, nunca
  dispara de novo.
- Duas travas de concorrência para o mesmo `job_id`, ambas 409: `pg_try_advisory_xact_lock` na
  janela de registro, e **execução já `running`** para o intervalo inteiro do despacho — o lock
  morre no commit, o `main.sh` não.
- Datas múltiplas (`dates_pattern: ["20260901","20260902"]`) viram UMA invocação
  (`--dates-pattern-files 20260901,20260902`) — é o `main.sh` quem serializa internamente.

## Verificar sem o container `legacy`

```bash
.venv/bin/python -m pytest tests/test_platform_api_ssh_backend.py       # build_invocation isolado
.venv/bin/python -m pytest tests/test_platform_api_ssh_integration.py   # contra o wrapper REAL, via bash
.venv/bin/python -m pytest tests/test_platform_api_contract.py         # estabilidade + shell-safety estático
```

## Verificar com Postgres + Keycloak (sem SSH)

```bash
docker compose up -d postgres
docker compose --profile auth up -d
export DATABASE_URL_MIGRATIONS=postgresql+psycopg://batch_migrator:batch_migrator_dev@localhost:5432/batch_catalog_test
.venv/bin/python -m pytest tests/test_platform_api_endpoints.py
```

Usa `InMemoryExecutionBackend` (mesma validação do backend real, sem SSH nenhum) contra Postgres
de teste real e tokens de verdade do Keycloak — pula sozinho se um dos dois não estiver no ar.

## Ponta a ponta (exige o container `legacy`)

```bash
docker compose --profile legacy up -d --build
set -a; . deploy/platform-api/local.env; set +a
platform-api
curl -X PATCH localhost:8000/jobs/<id>/status -H "Authorization: Bearer $TOKEN" \
  -d '{"desired_status":"disabled","reason":"teste"}'
```
