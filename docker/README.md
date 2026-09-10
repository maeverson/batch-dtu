# Ambiente de desenvolvimento local

Tudo o que a Fase 1 precisa, sem tocar no parque real.

```bash
cp .env.example .env
docker compose up -d                    # postgres + loki + grafana + prometheus + mailpit
docker compose ps
```

| Serviço | Porta | Papel | Profile |
|---|---|---|---|
| `postgres` | 5432 | catálogo, execuções, auditoria (ADR-003) | default |
| `loki` | 3100 | logs por `execution_id` | default |
| `grafana` | 3000 | consulta de logs e métricas | default |
| `prometheus` | 9090 | stand-in local do Mimir | default |
| `mailpit` | 8025 (UI) / 1025 (SMTP) | captura os steps `send_mail` | default |
| `keycloak` | 8080 | OIDC no lugar do Entra ID, com as roles `batch.*` | `auth` |
| `legacy` | 2222 (SSH) | host legado simulado: `main.sh` + wrapper `command=` | `legacy` |
| `minio` | 9000 / 9001 | destinos S3 dos contratos | `storage` |
| `sftp` | 2223 | destinos SFTP dos contratos | `storage` |
| `vault` | 8200 | aliases de conexão (Fase 2) | `secrets` |

```bash
docker compose --profile auth up -d      # + keycloak
docker compose --profile legacy up -d    # + host legado
docker compose --profile all up -d       # tudo
```

## O que cada peça resolve

**`postgres`** já sobe com a topologia de roles da produção: `batch_migrator` (dona do schema,
usada só pelas migrations), `batch_app` (a aplicação) e `batch_readonly`. A imutabilidade da
auditoria é imposta por privilégio, não por convenção — a migration que criar `audit_event`,
`job_contract_version` e `job_revision` precisa fazer `REVOKE UPDATE, DELETE` de `batch_app`.
Sobe também o banco `batch_catalog_test`, para a suíte não derrubar o de desenvolvimento.

**`keycloak`** importa o realm `batch-dtu` com as quatro roles da Fase 1 e quatro usuários
(senha = nome de usuário): `viewer`, `operator`, `operator-prod`, `admin-batch`. O escopo por
domínio e ambiente vem como claim (`batch_domains`, `batch_environments`), que é o mais próximo
do que o Entra ID vai entregar. Clients: `back-office` (público, PKCE) e `platform-api`
(confidencial, secret no `.env.example`).

**`legacy`** é a peça que evita desenvolver o backend da Fase 1 contra produção. Reproduz:

- `backoffice_svc` separado de `batch_user` (`docs/seguranca.md`)
- `authorized_keys` com `command="/usr/local/bin/batch-wrapper.sh",restrict` — não existe
  caminho de sessão interativa para a conta de serviço
- o wrapper revalidando **tudo** server-side: recusa metacaractere de shell, aceita só
  `main.sh` com as flags conhecidas, exige que o contrato esteja sob `processes/` sem
  travessia de diretório, e registra ALLOW/DENY em `/var/log/batch-wrapper.log`
- um `main.sh` stub com a semântica que a Fase 1 exercita: steps sequenciais com
  `stop_on_failed`, placeholders de data (`@@@YYYYMMDD@@@`, `@@@JULIANO@@@`…),
  `--manual-steps`, múltiplas datas **serializadas**, e `execution_id` no nome do log
- `batch_user` com a mesma chave **sem** `command=`, de propósito: é o contraste que mostra
  qual caminho é break-glass e qual é o da plataforma

A chave é gerada no primeiro start em `docker/legacy/keys/` (fora do git).

```bash
docker compose --profile legacy up -d --build
KEY=docker/legacy/keys/backoffice_svc_ed25519
FW=/opt2/batch_v2/batch-commons-framework

# pré-validação (equivalente a --validate-file)
ssh -i $KEY -p 2222 backoffice_svc@localhost \
  "$FW/main.sh --process-file $FW/processes/reportes/prd_aaa_col_rpt.json --validate-file"

# execução com data-alvo e execution_id, como a API vai montar
ssh -i $KEY -p 2222 backoffice_svc@localhost \
  "$FW/main.sh --process-file $FW/processes/base2/prd_aaa_col_bs2.json \
   --dates-pattern-files 20260909 --execution-id 7f3c1a90-0000-4000-8000-000000000001 --no-mail"

# o wrapper tem de RECUSAR (exit 42) — vale testar os três
ssh -i $KEY -p 2222 backoffice_svc@localhost "id"
ssh -i $KEY -p 2222 backoffice_svc@localhost "$FW/main.sh --process-file /etc/passwd"
ssh -i $KEY -p 2222 backoffice_svc@localhost "$FW/main.sh --process-file $FW/processes/../../etc/shadow"

# trilha do wrapper
docker compose exec legacy cat /var/log/batch-wrapper.log
```

Os contratos de fixture cobrem os dois padrões de paridade citados em
`docs/contrato-json.md`: `prd_aaa_col_bs2.json` com `stop_on_failed=true` em todos os steps
(falha interrompe) e `prd_bbb_pan_otr_mft.json` tolerante a falha, com `dev_fail` no step 2 para
o job seguir e terminar com status 1. Há também um contrato JSON inválido de propósito
(`prd_ccc_col_otr_invalido.json`) para o caminho de erro da validação.

## Comandos do dia a dia

```bash
# catálogo: importar o pacote coletado (não depende de banco ainda)
.venv/bin/catalog import seed/raw/batch-seed-<host>-<stamp>

# banco
psql "postgresql://batch:batch_dev@localhost:5432/batch_catalog"
docker compose exec postgres psql -U batch -d batch_catalog -c '\dn'

# logs no Loki, por execution_id
curl -s "http://localhost:3100/loki/api/v1/query_range" \
  --data-urlencode 'query={job="batch"} |= "7f3c1a90"' | jq .

# reset total (apaga volumes)
docker compose --profile all down -v
```

## Notas

- Portas podem colidir com serviços locais: todas são configuráveis no `.env`.
- `docker/legacy/keys/` e o `.env` estão no `.gitignore`.
- O `main.sh` daqui é **stub de desenvolvimento**, não o engine real. A paridade de verdade é
  do Executor v3, verificada por testes de contrato contra o `main.sh` de produção
  (`docs/contrato-json.md`) — este serve para desenvolver a API, o wrapper e a correlação de log.
