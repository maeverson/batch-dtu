# Segurança

## Identidade e acesso

- Autenticação **e autorização exclusivamente via Entra ID (OIDC)** para API e Back Office
  (decisão do cliente, 17/09/2026): a app role no token é a autorização, sem tabela de escopo
  consultada em runtime. Tokens de serviço de curta duração para integrações M2M.
- **O ambiente é do deploy, não da role**: uma instância da API serve um ambiente e um host
  (`PLATFORM_ENVIRONMENT`/`PLATFORM_HOST`). Quem separa PROD de UAT são grupos distintos no Entra
  atribuídos a `batch.operator-prod` e `batch.operator`, mais o fato de a instância de UAT não
  alcançar o host de PROD.
- **Consequência conhecida**: não há mais escopo por domínio. `batch.operator` alcança todo job do
  ambiente daquela instância. "Operador só de /reportes" exigiria app roles por domínio ou a volta
  de uma tabela de binding — a tabela `role_binding` continua no schema, mas nenhum código a lê.
- SSH humano direto a nós de execução = **break-glass**: credencial nomeada temporária, sessão gravada, revisão pós-acesso obrigatória.

## Roles mínimas (Fase 1)

| Role | Permissões |
|---|---|
| `batch.viewer` | Ver catálogo, execuções e logs |
| `batch.operator` | viewer + executar/reprocessar em instância não-PROD (UAT/TEST/DEV) |
| `batch.operator-prod` | Operar na instância PROD; confirmação reforçada obrigatória para steps de upload a cliente (`upload_remote`) |
| `batch.admin` | Gerir catálogo (`/admin/*`), ler auditoria, aprovar reprocessamentos sensíveis. **Não atravessa o recorte do deploy** |

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
- **Verificação da chave do host obrigatória** (`SSH_BACKEND_KNOWN_HOSTS`): sem ela o processo não
  sobe. O único escape (`SSH_BACKEND_ALLOW_UNKNOWN_HOSTS`) existe para o compose local e tem nome
  próprio justamente para aparecer em revisão de manifesto.
- `authorized_keys` restrito via `command=` a um script wrapper que só aceita invocações válidas de `main.sh`.
- A API constrói invocações a partir de **campos tipados** (processo do catálogo + steps + data); nunca interpola shell arbitrário. O wrapper server-side revalida.
- O wrapper aceita `--execution-id` da API mas **não o repassa ao `main.sh`** — o engine legado trata flag desconhecida como fatal. O identificador serve para o wrapper nomear o log correlacionado. O `main.sh` do `docker/legacy` é deliberadamente tão restritivo quanto o real nesse ponto: um stub mais permissivo que o original esconderia a falha até a primeira execução em UAT.
