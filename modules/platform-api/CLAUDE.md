# Módulo: Platform API

## Decisões tomadas (11/09/2026)

| Tema | Decisão |
|---|---|
| Stack | **Python + FastAPI** — reaproveita `catalog.db` (modelos, sessão, repositório) e a suíte que já roda contra Postgres real |
| Autorização | Escopo em **`role_binding` no catálogo**, não em grupos do Entra ID. A pergunta operacional é "quem pode executar este job", e ela se responde por domínio/ambiente/host do próprio job — precisa ser consultável e auditável junto do catálogo |
| Deploy | **EKS**, com VPC já configurada para alcançar os hosts legados por SSH |
| `PATCH /jobs/{id}/status` | Grava o estado desejado no catálogo **e** abre um `crontab_change_request`; a aplicação é manual na Fase 1 e a verificação é automática (ver abaixo) |

`NULL` em qualquer dimensão de escopo de `role_binding` significa **todas**. Conceder escopo
sempre restringe, nunca amplia.

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

Contexto ao trabalhar aqui:
- Leia `../../docs/api/platform-api.md` (contrato REST) e `../../docs/contrato-json.md` antes de qualquer implementação.
- **O contrato REST não muda entre fases** — só o backend de execução (Fase 1: SSH parametrizado; Fase 2: enfileiramento no orchestrator). Isole o backend atrás de uma interface (`ExecutionBackend`) desde o dia 1.
- **Nunca interpole shell arbitrário.** Linha de comando montada exclusivamente de campos tipados validados contra o catálogo.
- Toda ação de escrita gera `audit_event` (append-only) e toda execução gera registro `execution` com `execution_id` propagado aos logs.
- Autenticação: Entra ID (OIDC). Autorização: roles `batch.*` com escopo domínio/ambiente (ver `../../docs/seguranca.md`).
