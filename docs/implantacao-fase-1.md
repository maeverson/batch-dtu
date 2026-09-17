# Implantação da Fase 1 — passo a passo

Runbook de implantação do que a Fase 1 entrega: **Platform API + Back Office +
catálogo + observabilidade**, operando por SSH parametrizado contra o host do
`main.sh`, com identidade e RBAC no **Entra ID** do cliente e observabilidade no
**New Relic**.

Público: quem vai implantar (infra/plataforma + um owner do parque batch).

**Começa por UAT** (`Batch-DTU` / `com-ins-bch-mdw-dtu-1` / `172.21.86.76`), por
decisão de homologação. PROD (`srv-sftp-2` / `172.17.37.120`) é o **mesmo
procedimento com outro arquivo de ambiente**, depois do ensaio fim-a-fim em UAT.

> **O que este documento NÃO é**: não é o plano de descomissionamento (Fase 2).
> Ao final da Fase 1 o cron e o `main.sh` continuam rodando exatamente como
> hoje — o que muda é que **toda operação manual** deixa de ser SSH humano e
> passa pela API, autenticada e auditada.

---

## 0. Decisões que já estão no código

As seis lacunas levantadas no levantamento anterior foram fechadas e
implementadas. Ficam registradas aqui porque cada uma mudou o desenho:

| # | Decisão | Onde está |
|---|---|---|
| **G1** | **Uma instância serve UM ambiente e UM host.** UAT e PROD são deploys distintos da mesma imagem. A instância de UAT não lista, não executa e não administra job de PROD — **nem com `batch.admin`** | `PLATFORM_ENVIRONMENT`/`PLATFORM_HOST` em `src/platform_api/config.py`; `Scope.serves()` em `authz.py` |
| **G2** | **RBAC vem inteiro do Entra ID.** A app role no token É a autorização; não há mais consulta a `role_binding`. Keycloak fica só no `docker-compose.yaml` local | `src/platform_api/authz.py`, `security.py` |
| **G3** | **Verificação de chave de host obrigatória, agnóstica de ambiente.** Sem `SSH_BACKEND_KNOWN_HOSTS` o processo **não sobe** — o único escape (`SSH_BACKEND_ALLOW_UNKNOWN_HOSTS`) tem nome próprio para aparecer em revisão de manifesto | `SSHBackendSettings.validate()` |
| **G4** | **O host do `main.sh` é configuração**, e a relação de confiança é chave pública + `known_hosts` | `SSH_BACKEND_*` em `deploy/platform-api/*.env.example` |
| **G5** | **`POST /executions` responde 200 assim que despacha**, com `status: running`. O acompanhamento é no New Relic, pelo `execution_id` | `routers/executions.py` (`BackgroundTasks`), `telemetry.py` |
| **G6** | **CRUD de administração** (`/admin/*` + tela no Back Office), só `batch.admin` | `routers/admin.py`, `frontend/src/pages/AdminPage.tsx` |

Duas consequências que valem ser ditas antes de alguém descobrir operando:

- **Não existe mais escopo por domínio.** Um `batch.operator` alcança todo job
  do ambiente daquela instância. As app roles do Entra são planas; se a
  operação precisar de "operador só de `/reportes`", isso volta a exigir uma
  dimensão que o Entra não carrega hoje.
- **O 200 não é o desfecho.** Quem disparava e via o exit code na resposta
  agora vê "despachado". Por isso o alerta de execução presa em `running`
  (seção 8) não é opcional: ele é o que substitui aquele feedback.

---

## 1. Pré-requisitos

1. **RDS PostgreSQL** provisionado (ADR-003). Retenção e imutabilidade da
   trilha continuam **em aberto** com compliance — feche antes da carga, porque
   `audit_event` é append-only por trigger e mudar particionamento depois é
   migração com dado dentro.
2. **Rede**: a subnet dos pods alcança `22/tcp` do host alvo. É a única porta
   que a Fase 1 abre em direção ao legado.
3. **Entra ID**: alguém com `Application Administrator` para criar as app
   registrations e um `Privileged Role Administrator` para o *admin consent*.
4. **New Relic**: conta, license key (ingestão) e uma User Key (consulta).
5. **Break-glass** combinado para os passos no host (seção 6): credencial
   nomeada temporária, sessão gravada, janela e ticket registrados.

---

## 2. Ordem de implantação

```
banco → catálogo carregado → Entra ID → Platform API → host UAT (backoffice_svc)
  → New Relic → Back Office → ensaio fim-a-fim em UAT → repetir para PROD
```

Cada etapa tem uma verificação que precisa passar antes da seguinte.

---

## 3. Banco (RDS PostgreSQL)

```bash
set -a; . deploy/catalog/ingest.env; set +a      # variáveis sobem com o job
alembic upgrade head
```

As três roles são as mesmas do init local (`docker/postgres/init`):
`batch_migrator` (dona do schema), `batch_app` (a API, **sem** UPDATE/DELETE nas
tabelas append-only) e a role de leitura para dashboards.

**Verificação**: `alembic current` = head, e um `UPDATE catalog.audit_event SET
...` conectado como `batch_app` **falha**. Se esse UPDATE funcionar, o `REVOKE`
não foi aplicado e a auditoria não vale nada.

---

## 4. Carga do catálogo

Duas fontes, propósitos diferentes — as duas persistem no mesmo RDS:

### 4.a Pacote do coletor (completo: job + agenda + crontab + reconciliação)

```bash
# no host, em sessão break-glass registrada — seed/collect/README.md
ssh -t <voce>@172.21.86.76 && sudo -i     # ... collect-seed.sh, baixa o tar.gz

catalog import seed/raw/batch-seed-<host>-<stamp> --out seed/raw/reports
# resolver clients.todo-<host>.yaml — NÃO adivinhar cliente
catalog load    seed/raw/batch-seed-<host>-<stamp> --actor <voce>@contabilizei.com.br
catalog reconcile seed/raw/batch-seed-<host>-<stamp> --actor <voce>@contabilizei.com.br
```

### 4.b Árvore de contratos (`catalog ingest-processes`)

Quando a fonte é o diretório de contratos e não um pacote de coleta — por
exemplo `/opt2/batch_v2/batch-commons-framework/processes/base2`, onde **cada
`.json` é o contrato de um job**:

```bash
set -a; . deploy/catalog/ingest.env; set +a
catalog ingest-processes /opt2/batch_v2/batch-commons-framework/processes/base2 \
  --host srv-sftp-2 --environment PROD \
  --actor <voce>@contabilizei.com.br --dry-run     # confira o relatório
catalog ingest-processes /opt2/batch_v2/batch-commons-framework/processes/base2 \
  --host srv-sftp-2 --environment PROD --actor <voce>@contabilizei.com.br
```

Lendo de uma cópia local, mantenha o caminho do host:
`--framework-root /opt2/batch_v2/batch-commons-framework`. O `contract_path`
gravado precisa ser o que o host vê — é ele que vai para `--process-file`.

O que este comando **não** faz, de propósito: não inventa agenda (sem crontab
não há como saber quando o job roda — entra como `on_demand`, sem
`job_schedule`) e não apaga job cujo contrato sumiu do diretório (isso é
`catalog reconcile`). Idempotente: rodar duas vezes não duplica nada.

**Verificação**: `catalog reconcile` sai 0 no host alvo e a amostragem por
domínio (`catalog sample <host>`) tem conferência humana registrada.

---

## 5. Entra ID — identidade **e** RBAC

### 5.1 App registration da API

1. **App registrations → New registration**: `batch-platform-api`. Sem redirect URI.
2. **Expose an API** → Application ID URI `api://<api-client-id>` → **Add a
   scope**: `access_as_user`.
3. **Manifest**: `"accessTokenAcceptedVersion": 2`. Sem isso o token sai v1, com
   `iss` `https://sts.windows.net/<tenant>/`, e nenhum token é aceito.
4. **App roles** (`appRoles` no manifest), com estes `value` exatos — são os que
   `authz.py` compara:

   | `value` | pode |
   |---|---|
   | `batch.viewer` | ver catálogo, execuções e logs |
   | `batch.operator` | + executar/reprocessar (instância não-PROD) |
   | `batch.operator-prod` | + executar/reprocessar (instância PROD) |
   | `batch.admin` | + CRUD de catálogo e leitura de auditoria |

   Hífen em `operator-prod`, nunca underscore.

5. **Enterprise application → Users and groups**: atribua os grupos do cliente
   às app roles. **É aqui, e só aqui, que se concede acesso.** Não há mais
   `INSERT` em tabela nenhuma.

> **Separação PROD × UAT**: como a instância é que define o ambiente, quem pode
> operar PROD é quem tem `batch.operator-prod`. Mantenha grupos distintos
> (`batch-operadores-uat`, `batch-operadores-prod`) — é o controle que impede
> alguém de homologação disparar contra cliente real.

### 5.2 App registration do Back Office (SPA)

1. `batch-back-office`, plataforma **Single-page application** (PKCE, **sem**
   client secret).
2. Redirect URI: `https://<host-do-back-office>/callback`.
3. **API permissions** → *My APIs* → `batch-platform-api` → `access_as_user` →
   **Grant admin consent**.

### 5.3 Variáveis

Já prontas em `deploy/platform-api/uat.env.example` (API) e
`deploy/back-office/uat.env.example` (SPA). Os dois pontos que mais custam:

- `OIDC_JWKS_URI` é **obrigatório** — o default é derivado no formato Keycloak,
  que no Entra não existe.
- `VITE_OIDC_SCOPE` **precisa** incluir `api://<api-client-id>/access_as_user`.
  Sem isso o login conclui, a UI parece viva, e toda chamada volta 401.

### 5.4 `OIDC_SUBJECT_CLAIM` — decida com cuidado

`oid` (GUID imutável do tenant) é o default recomendado nos arquivos de deploy.
`preferred_username`/`upn` mudam com casamento, troca de sobrenome ou migração
de domínio de e-mail — e a trilha passa a apontar para alguém que "não existe".
`OIDC_DISPLAY_NAME_CLAIM` grava o nome legível junto, para a auditoria continuar
legível.

### 5.5 Verificação

```bash
curl -s https://<host-da-api>/me -H "Authorization: Bearer $TOKEN"
```

Deve devolver `subject`, `roles` (as `batch.*`), `environment` e `host` —
`environment`/`host` são os **da instância**, e conferi-los aqui é como se
descobre um deploy apontado para o parque errado.

---

## 6. Host do `main.sh` — conta de serviço e wrapper

Nada aqui altera `main.sh`, crontab ou contratos: só **acrescenta** uma conta de
serviço restrita. Comece pelo host de UAT (`172.21.86.76`).

### 6.1 Conta e permissões de log

```bash
useradd -r -m -g batch -s /bin/bash backoffice_svc   # distinta de batch_user
install -d -m 2775 -o batch_user -g batch \
  /opt2/batch_v2/batch-commons-framework/logs/backoffice
```

O setgid (`2775`) não é detalhe: sem escrita do grupo `batch` nessa árvore, o
`tee` do wrapper falha e **nenhum** log correlacionado chega ao New Relic.

### 6.2 Wrapper

Instale `docker/legacy/batch-wrapper.sh` como `/usr/local/bin/batch-wrapper.sh`
(`root:root 0755`), conferindo `FW_ROOT=/opt2/batch_v2/batch-commons-framework`
contra o host. Ele é o controle server-side: recusa metacaractere de shell,
tokeniza sem shell, exige que o executável seja o `main.sh` do framework e que o
contrato esteja sob `processes/` sem travessia, e aceita apenas as seis flags
previstas. Adicione `logrotate` para `/var/log/batch-wrapper.log` — é a trilha
ALLOW/DENY do canal e cresce sem limite.

### 6.3 `authorized_keys` restrito

```bash
install -d -m 0700 -o backoffice_svc -g batch /home/backoffice_svc/.ssh
printf 'command="/usr/local/bin/batch-wrapper.sh",restrict %s\n' "$(cat backoffice_svc_ed25519.pub)" \
  > /home/backoffice_svc/.ssh/authorized_keys
chown backoffice_svc:batch /home/backoffice_svc/.ssh/authorized_keys
chmod 0600 /home/backoffice_svc/.ssh/authorized_keys
```

A privada vai para o vault e é montada no pod como arquivo `0600`. `restrict`
desliga port/agent forwarding, X11 e TTY — não existe caminho interativo.

### 6.4 `known_hosts` (a outra metade da confiança)

```bash
ssh-keyscan -t ed25519 172.21.86.76 > /etc/batch/known_hosts
```

Confira o fingerprint contra `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`
rodado **no próprio servidor**, pela sessão break-glass. Keyscan sem conferência
não protege de nada. O arquivo vai para o pod em `SSH_BACKEND_KNOWN_HOSTS`;
sem ele a API **não sobe**.

### 6.5 Verificação — os três testes que separam "funciona" de "está seguro"

```bash
# 1. pré-validação de um contrato real (não executa nada)
ssh -i <chave> backoffice_svc@172.21.86.76 \
  '/opt2/batch_v2/batch-commons-framework/main.sh --process-file <...>.json --validate-file'
# 2. sessão interativa → recusada (exit 42)
ssh -i <chave> backoffice_svc@172.21.86.76
# 3. comando arbitrário → exit 42 + linha DENY em /var/log/batch-wrapper.log
ssh -i <chave> backoffice_svc@172.21.86.76 'id'
```

Se 2 ou 3 funcionarem, **pare**: o `command=` não está ativo e a conta virou SSH
irrestrito.

---

## 7. Platform API

```bash
# EKS: os valores viram Secret/ConfigMap montado como env do pod.
# Nada é exportado no shell do host (ver deploy/README.md).
envFrom: deploy/platform-api/uat.env
```

Pontos de deploy:

- **Chave privada** montada do vault (`0600`, dono = usuário do processo); nunca
  em imagem nem ConfigMap.
- **Réplicas**: seguro. O lock é `pg_try_advisory_xact_lock` no banco, e há uma
  segunda trava (execução `running` no mesmo job → 409) que cobre o intervalo
  inteiro do despacho, que o lock não alcança mais.
- **Timeout do ingress**: deixou de ser crítico — a requisição termina no
  despacho, não no fim do job. Continua valendo um timeout folgado para a
  pré-validação (`--validate-file`), que é síncrona.

**Verificação**: `GET /health` devolve `environment`/`host` esperados; `GET
/jobs` com token real lista só o ambiente da instância; `GET /audit-events` sem
`batch.admin` devolve 403.

---

## 8. Observabilidade — New Relic

Duas pontas, uma chave (`execution_id`), detalhadas em `deploy/newrelic/README.md`:

1. **APM no pod** (`newrelic.ini` + `NEW_RELIC_*`): emite o evento
   `BatchExecution` por execução, com job, ambiente, quem pediu e desfecho.
2. **Agente de log no host** (`logging.d/batch-backoffice.yml`): embarca
   `logs/backoffice/<domínio>/<processo>.<execution_id>.log`.
3. **Regra de parsing** (uma vez, na conta): extrai `execution_id` do
   `filePath`. Sem ela o log chega e `GET /executions/{id}/logs` volta vazio.
4. **Timestamp da linha**, não o de ingestão — senão a linha cai fora da janela
   consultada e a execução aparece "sem log".

**Alerta obrigatório desta fase** (é o que substitui o exit code na resposta):

```sql
SELECT count(*) FROM BatchExecution WHERE status = 'running' SINCE 30 minutes ago
```

**Verificação**: uma execução manual em UAT devolve linhas em
`GET /executions/{id}/logs` e aparece em `BatchExecution` com o mesmo
`execution_id`.

---

## 9. Back Office

```bash
cp deploy/back-office/uat.env.example frontend/.env.production.local   # ajuste
cd frontend && npm ci && npm run build      # dist/ → CDN/S3+CloudFront ou nginx
```

Vite embute as variáveis em **build time**: trocar o arquivo depois não tem
efeito, é preciso rebuildar. Três coisas precisam bater exatamente:
`VITE_OIDC_REDIRECT_URI`, o redirect URI da app registration e
`CORS_ALLOW_ORIGINS` da API. Divergência em qualquer uma dá erro no navegador e
nada no log da API.

A barra superior mostra o **ambiente da instância** (etiqueta vermelha em PROD)
e a aba **Administração** aparece só para `batch.admin`.

**Verificação**: login completo em navegador real, catálogo lista, o modal de
execução exige confirmação de data-alvo. Esse teste end-to-end **nunca foi
feito** (não há browser headless no ambiente de desenvolvimento —
`modules/back-office/OPERACAO.md`) e é pré-requisito do marco.

---

## 10. Administração (`batch.admin`)

O que a tela faz: criar job, editar metadados (owner, criticidade, SLA,
cliente), desativar e publicar nova versão de contrato (validada pelo schema,
append-only por hash). Três regras embutidas:

- **Ambiente e host não são campos** — vêm da instância.
- **Motivo é obrigatório** em toda escrita; vai para `audit_event`.
- **`DELETE` não apaga.** Desativa no catálogo, com motivo. E desativar no
  catálogo **não para o cron**: enquanto a entrada existir no crontab o job
  continua rodando — quem fecha esse loop é `PATCH /jobs/{id}/status`, que abre
  `crontab_change_request`.

---

## 11. Ensaio em UAT e o marco da Fase 1

Nesta ordem, sem pular:

1. Execução manual de um job **sem** `upload_remote` pela UI. Confira:
   `execution` registrada, `audit_event` `execution.dispatch` +
   `execution.completed`, log no New Relic, linha ALLOW no `batch-wrapper.log`.
2. **Reprocesso Zinli/MFTech em UAT**, com `upload_remote`. É o ensaio que
   existe para steps de upload não estrearem contra cliente real.
3. Só então repita as seções 6 a 9 para PROD, com
   `deploy/platform-api/prod.env` e `deploy/back-office/prod.env`.

Marco 🏁 fechado quando: reprocesso fim-a-fim pelo Back Office em PROD, **zero**
execução manual por SSH fora do break-glass (cruze o log do sshd com a tabela
`execution`), e 100% das execuções manuais com registro de auditoria.

---

## 12. Rollback e break-glass

- **Rollback da Fase 1 não tem janela**: cron e `main.sh` nunca pararam.
  Desligue API e Back Office e a operação volta ao que era.
- **Rollback só do canal**: remova `/home/backoffice_svc/.ssh/authorized_keys`.
  Um comando, efeito imediato, nenhum job afetado.
- **Revogar uma pessoa**: tire o grupo/app role no Entra. Com RBAC 100% no IdP,
  esse é o único lugar — e vale para os dois ambientes.
- **Break-glass** (`docs/seguranca.md`): credencial nomeada temporária, sessão
  gravada, revisão posterior. Toda coleta e todo passo da seção 6 entram nessa
  categoria.

---

## 13. Checklist

```
[ ] ADR-003 fechado com compliance (retenção/imutabilidade)
[ ] alembic upgrade head; UPDATE em audit_event como batch_app FALHA
[ ] catálogo carregado (pacote e/ou ingest-processes); reconcile sai 0
[ ] app registrations criadas, accessTokenAcceptedVersion=2, app roles atribuídas
[ ] grupos separados para operador UAT e operador PROD
[ ] GET /me com token real do Entra devolve roles + environment/host esperados
[ ] backoffice_svc criado; sessão interativa e comando arbitrário recusados (exit 42)
[ ] logs/backoffice gravável pelo grupo batch (2775)
[ ] known_hosts com fingerprint conferido NO host; API sobe sem o escape inseguro
[ ] agente New Relic no host + regra de parsing de execution_id + timestamp da linha
[ ] alerta de execução presa em `running` configurado
[ ] SPA buildada com VITE_OIDC_SCOPE incluindo o escopo da API
[ ] login PKCE completo testado em navegador real
[ ] reprocesso com upload_remote ensaiado em UAT ANTES de qualquer deploy PROD
```
