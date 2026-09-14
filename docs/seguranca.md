# Segurança

## Identidade e acesso

- Autenticação **exclusivamente via Entra ID (OIDC)** para API e Back Office.
- RBAC com escopo por domínio e ambiente. Tokens de serviço de curta duração para integrações M2M.
- SSH humano direto a nós de execução = **break-glass**: credencial nomeada temporária, sessão gravada, revisão pós-acesso obrigatória.

## Roles mínimas (Fase 1)

| Role | Permissões |
|---|---|
| `batch.viewer` | Ver catálogo, execuções e logs |
| `batch.operator` | viewer + executar/reprocessar em UAT/TEST |
| `batch.operator-prod` | Operar em PROD; confirmação reforçada obrigatória para steps de upload a cliente (`upload_remote`) |
| `batch.admin` | Gerir catálogo, escopo de roles, aprovar reprocessamentos sensíveis |

## Segredos

- Vault como fonte única; rotação automática e monitoramento de validade.
- Alertas proativos para chaves GPG de cliente próximas de expirar (hoje só descobertas na falha do step `encrypt`).
- Tokens de observabilidade (Grafana Cloud `glc_...`) saem de unit files do systemd para parameter store / vault.

## Superfície de execução

- Executores com permissão mínima por connector (least privilege por alias).
- `execute_command` remoto restrito a hosts e scripts registrados no catálogo.
- Trilhas de auditoria compatíveis com requisitos regulatórios (arquivos de clearing e dados de portador de cartão).

## Fase 1 — hardening do canal SSH da API

- Conta de serviço dedicada `backoffice_svc` (distinta de `batch_user`).
- `authorized_keys` restrito via `command=` a um script wrapper que só aceita invocações válidas de `main.sh`.
- A API constrói invocações a partir de **campos tipados** (processo do catálogo + steps + data); nunca interpola shell arbitrário. O wrapper server-side revalida.
- O wrapper aceita `--execution-id` da API mas **não o repassa ao `main.sh`** — o engine legado trata flag desconhecida como fatal. O identificador serve para o wrapper nomear o log correlacionado. O `main.sh` do `docker/legacy` é deliberadamente tão restritivo quanto o real nesse ponto: um stub mais permissivo que o original esconderia a falha até a primeira execução em UAT.
