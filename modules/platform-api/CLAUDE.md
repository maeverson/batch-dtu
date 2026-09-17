# Módulo: Platform API

## Decisões tomadas (11/09/2026)

| Tema | Decisão |
|---|---|
| Stack | **Python + FastAPI** — reaproveita `catalog.db` (modelos, sessão, repositório) e a suíte que já roda contra Postgres real |
| Autorização | **Entra ID, inteira** (revisado em 17/09/2026 pelo cliente; antes era `role_binding` no catálogo). A app role do token autoriza; a dimensão ambiente/host vem do DEPLOY, não de uma linha de banco. `role_binding` continua no schema e **nenhum código a lê** |
| Escopo do deploy | **Uma instância, um ambiente, um host** (`PLATFORM_ENVIRONMENT`/`PLATFORM_HOST`). UAT e PROD são deploys distintos. `Scope.serves()` corta antes de qualquer role — inclusive `batch.admin` |
| Execução | `POST /executions` **responde 200 ao despachar**, não ao terminar. A pré-validação (`--validate-file`) continua síncrona; o acompanhamento é no New Relic por `execution_id` |
| Administração | CRUD de catálogo em `/admin/*`, só `batch.admin`. `DELETE` desativa, nunca apaga |
| Deploy | **EKS**, com VPC já configurada para alcançar os hosts legados por SSH |
| `PATCH /jobs/{id}/status` | Grava o estado desejado no catálogo **e** abre um `crontab_change_request`; a aplicação é manual na Fase 1 e a verificação é automática (ver abaixo) |

**Como se concede acesso agora**: grupo no Entra ID atribuído à app role. Não existe mais
`INSERT` em tabela nenhuma — e revogar é tirar o grupo, num lugar só.

**O que se perdeu, explicitamente**: escopo por domínio. `batch.operator` alcança todo job do
ambiente da instância. Se a operação pedir "operador só de /reportes", a decisão volta à mesa —
app roles por domínio no Entra, ou a tabela de binding de volta.

## Ciclo de mudança de agendamento (Fase 1)

    pending  --(operador aplica a linha-alvo)-->  applied
    pending/applied  --(reconciliação detecta o estado desejado)-->  verified

- A API **não escreve no crontab** na Fase 1 (invariante 2). Um bug num arquivo de 597 linhas
  editadas à mão é catastrófico, e o crontab ainda tem entradas fora do catálogo.
- A instrução gerada é a **linha-alvo**, não um diff: um diff calculado no PATCH pode não aplicar
  mais quando o operador executar.
- A linha carrega o marcador `#BO:<job_id>:<change_id>` — âncora que a reconciliação usa para
  fechar o loop e que substitui a prosa livre que o parque já usa hoje.
- A verificação é por **detecção**, não por declaração: exigir que o operador volte à UI dizer
  "apliquei" é o passo que na prática ninguém faz.
- Divergência **sem** request aberta é `drift-nao-gerenciado` (erro) — o detector de gente
  editando crontab por fora.
- Pendência além do SLA vira `expired` e alarma, porque desabilitar no catálogo **não para o
  cron**: até a aplicação, o job continua disparando.

Na Fase 2 o executor do apply deixa de ser humano e vira pipeline; a máquina de estados sobrevive.

**Operação do serviço**: `OPERACAO.md` neste diretório — como subir, autenticar, os endpoints, e
como verificar sem depender do container `legacy`. **Implantação**:
`../../docs/implantacao-fase-1.md` (Entra ID real, host do `main.sh`, New Relic) e `deploy/`.

Contexto ao trabalhar aqui:
- Leia `../../docs/api/platform-api.md` (contrato REST) e `../../docs/contrato-json.md` antes de qualquer implementação.
- **O contrato REST não muda entre fases** — só o backend de execução (Fase 1: SSH parametrizado; Fase 2: enfileiramento no orchestrator). Isole o backend atrás de uma interface (`ExecutionBackend`) desde o dia 1.
- **Nunca interpole shell arbitrário.** Linha de comando montada exclusivamente de campos tipados validados contra o catálogo.
- Toda ação de escrita gera `audit_event` (append-only) e toda execução gera registro `execution` com `execution_id` propagado aos logs.
- Autenticação e autorização: Entra ID (OIDC), roles `batch.*` do token (ver `../../docs/seguranca.md`).
