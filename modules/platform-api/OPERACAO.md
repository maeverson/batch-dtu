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
export DATABASE_URL=postgresql+psycopg://batch_app:batch_app_dev@localhost:5432/batch_catalog
platform-api   # sobe uvicorn em :8000 — docs interativos em /docs
```

## Autenticação (Keycloak local)

Realm `batch-dtu`, roles `batch.viewer/operator/operator-prod/admin`, usuários de teste
`viewer/operator/operator-prod/admin-batch` (senha = usuário). Pegar um token:

```bash
curl -s -X POST http://localhost:8080/realms/batch-dtu/protocol/openid-connect/token \
  -d grant_type=password -d client_id=platform-api -d client_secret=platform-api-dev-secret \
  -d username=operator-prod -d password=operator-prod -d scope=openid \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])'
```

O token carrega `realm_access.roles` (a entitlement) e `preferred_username` (o `subject` de
`role_binding`). **As claims `batch_domains`/`batch_environments` do Keycloak são ignoradas de
propósito** — o escopo fino vem só de `role_binding`, nunca do IdP (ver `CLAUDE.md`). Sem uma
linha em `role_binding` para o `subject`, o usuário não opera nem vê nada, mesmo com a role certa
no token.

Trocar para Entra ID real: variáveis `OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_ROLES_CLAIM` (Entra
tipicamente usa `roles` direto, não `realm_access.roles`), `OIDC_SUBJECT_CLAIM`. Nada mais muda.

## Conceder acesso — `role_binding`

Não existe endpoint de administração ainda (Fase 1 embrionária) — inserir direto:

```sql
INSERT INTO catalog.role_binding (subject, subject_type, role, scope_domain, scope_environment, granted_by, created_by)
VALUES ('operator', 'user', 'batch.operator', NULL, 'UAT', 'voce@dev', 'voce@dev');
```

`subject_type` e `created_by` são `NOT NULL` (`catalog/db/models.py`) — sem eles o insert falha
com `NotNullViolation`. `role` é validado por `CHECK` contra `batch.viewer`, `batch.operator`,
`batch.operator-prod`, `batch.admin` (com hífen em `operator-prod`, não underscore).

`NULL` numa dimensão = essa dimensão não restringe. Um binding sem `scope_environment` cobre
PROD e UAT; um sem NENHUM escopo cobre tudo daquela role.

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
| `GET /executions/{id}/logs` | Proxy Loki por `execution_id` (funciona já — falta o agente que embarca os `.log` do legado, é `observability`, Etapa 1.3) |
| `GET /change-requests?state=&host=` | Worklist do Back Office |
| `POST /change-requests/{id}/cancel` | Desiste de uma mudança pendente |
| `GET /audit-events?...` | Exige `batch.admin` |
| `GET /me` | Subject, roles e domínios/ambientes visíveis do token atual (Back Office) |

### `POST /executions` — o que é obrigatório

- `confirm_target_dates: true` sempre.
- `confirm_upload_remote: true` **quando** o contrato tem `upload_remote`/`download_remote` **e**
  o job é PROD — confirmação reforçada real, não cosmética: sem ela, 422.
- Antes de executar de verdade, a API roda **pré-validação** (`--validate-file`) via o mesmo
  `ExecutionBackend` — se ela falhar, nada é persistido e a resposta é 422. Duas chamadas SSH por
  execução bem-sucedida (validação + execução), de propósito.
- `idempotency_key` opcional: reenvio da mesma chave devolve a execução já registrada, nunca
  dispara de novo.
- Duas requisições concorrentes para o **mesmo `job_id`** — a segunda recebe 409
  (`pg_try_advisory_xact_lock`, sem fila, sem espera).
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
export SSH_BACKEND_HOST=localhost SSH_BACKEND_PORT=2222
export SSH_BACKEND_KEY=docker/legacy/keys/backoffice_svc_ed25519
platform-api
curl -X PATCH localhost:8000/jobs/<id>/status -H "Authorization: Bearer $TOKEN" \
  -d '{"desired_status":"disabled","reason":"teste"}'
```
