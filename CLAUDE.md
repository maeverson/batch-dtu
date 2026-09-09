# Plataforma de Jobs Agendados (Batch DTU / Batch V2)

Este repositório contém a documentação e as especificações para a evolução do parque de jobs agendados, em 3 fases (strangler fig sobre o legado `cron + main.sh`).

## O que é este projeto

O legado é um framework shell (`main.sh`) disparado por cron no host `com-ins-bch-mdw-dtu-1`, com **509 entradas** de crontab (390 ativas, 119 desabilitadas/on-demand), interpretando **contratos JSON declarativos** por job. A evolução substitui gradualmente agendamento, execução, segurança, auditoria e observabilidade — **preservando o contrato JSON como interface estável**.

- **Fase 1** — Back Office + execução controlada (RBAC + auditoria). Cron e `main.sh` continuam; toda operação manual passa pela API (SSH parametrizado contra o legado).
- **Fase 2** — Scheduler gerenciado + Executor v3 containerizado; descomissionamento dos servidores.
- **Fase 3** — Event-driven, DAGs, SLAs/alertas de ausência, reprocessamento self-service com aprovação, onboarding no-code de jobs.

## Invariantes (NUNCA violar)

1. **Contrato JSON é interface estável.** Nenhuma fase reescreve os 509 contratos em massa. Extensões são aditivas e versionadas (`schema_version`). Ver `docs/contrato-json.md`.
2. **Strangler fig, não big bang.** O legado continua operando até a última wave da Fase 2. Rollback de qualquer wave = reativar entrada no crontab + pausar schedule gerenciado.
3. **Execução como dado.** Toda execução (agendada, manual, reprocesso, evento) gera registro estruturado: quem, quando, qual processo, quais steps, data-alvo, resultado, link de logs (`execution_id`).
4. **Nenhum acesso humano direto ao plano de execução.** Operação via Back Office, autenticada por Entra ID e autorizada por role. SSH é exceção break-glass auditada.
5. **Segredos em vault, nunca em arquivos ou contratos.** Contratos referenciam apenas *aliases*; o executor resolve em runtime.
6. **Observabilidade nativa, desacoplada de arquivos.** Base Grafana Cloud (Loki/Mimir) permanece; muda a fonte: logs estruturados por execução com `execution_id`, métricas emitidas pelo executor, alertas de execução *ausente* (não só de erro).
7. **Idempotência e reprocessamento como cidadãos de primeira classe.** Semântica `--dates-pattern-files` + `--manual-steps` vira API formal, com validação de data-alvo e salvaguardas contra reenvio acidental a clientes.

## Regras para desenvolvimento (Claude Code)

- **Contrato da Platform API não muda entre fases.** Na Fase 1 ela traduz para SSH parametrizado; na Fase 2, enfileira no orchestrator. O Back Office nunca deve conhecer o mecanismo de execução.
- **A API nunca interpola shell arbitrário.** Invocações são construídas a partir de campos tipados (processo do catálogo + steps + data); o wrapper server-side revalida (Fase 1).
- **Catálogo é a fonte da verdade** para agendamento, RBAC (escopo domínio/cliente) e dashboards. A partir da Fase 2, crontab é artefato gerado/reconciliado, nunca editado manualmente.
- **Paridade funcional do Executor v3 com `main.sh` é obrigatória** e verificada por suíte de testes de contrato + shadow execution com diff de artefatos antes de cada cutover.
- Steps que fazem upload a clientes (`upload_remote`) exigem confirmação reforçada (Fase 1) e aprovação two-person (Fase 3).
- Ambientes (PROD/UAT/TEST/DEV) devem ser tratados como dimensão explícita em catálogo, RBAC e (Fase 3) compute isolado — o legado os mistura num único crontab.

## Mapa do repositório

| Caminho | Conteúdo |
|---|---|
| `docs/` | Visão geral, princípios, contrato JSON, modelo de dados, segurança, observabilidade, migração, riscos |
| `docs/adr/` | Decisões abertas (ADR-001 a 004) e template |
| `docs/api/` | Contrato REST da Platform API (rascunho OpenAPI) |
| `modules/<módulo>/` | `CLAUDE.md` (contexto do módulo) + `SPEC.md` (requisitos e critérios de aceite) |
| `backlog/` | Backlog e critérios de aceite por fase |

## Glossário rápido

- **Contrato JSON**: definição declarativa de um job em `/processes/*` (steps, `stop_on_failed`, placeholders de data).
- **`main.sh`**: engine legado que interpreta contratos. Flags relevantes: `--process-file`, `--manual-steps`, `--dates-pattern-files`, `--validate-file`, `--no-mail`.
- **Placeholders de data**: `@@@YYYY@@@`, `@@@JULIANO@@@`, `@@@YYYYMMDD@@@` etc., resolvidos em runtime.
- **Waves**: ordem de migração da Fase 2 por domínio, do menor ao maior risco: `/otros` → `/reportes` → `/saldos` + `/transacciones` → `/emisiones` + `/emboces` → `/base2` (shadow execution obrigatória).
- **Break-glass**: acesso SSH excepcional, com credencial nomeada temporária, sessão gravada e revisão posterior.
