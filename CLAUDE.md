# Plataforma de Jobs Agendados (Batch DTU / Batch V2)

Este repositório contém a documentação e as especificações para a evolução do parque de jobs agendados, em 3 fases (strangler fig sobre o legado `cron + main.sh`).

> **Antes de iniciar qualquer trabalho**: consulte o `ROADMAP.md` na raiz — ele define a ordem de execução dos módulos, as dependências entre etapas e o estado atual (checkboxes). Ao concluir uma entrega, atualize os checkboxes correspondentes no roadmap.

## O que é este projeto

O legado é um framework shell (`main.sh`) disparado por cron no host **`Batch-Prod-srv-sftp-2-120` (`172.17.37.120`, hostname `srv-sftp-2`)**, com **597 linhas de job** no crontab (393 ativas, 204 desabilitadas/on-demand), que consolidam em **527 jobs distintos** — medido na coleta de 09/2026; a premissa inicial de 509 entradas estava defasada. O framework interpreta **contratos JSON declarativos** por job. A evolução substitui gradualmente agendamento, execução, segurança, auditoria e observabilidade — **preservando o contrato JSON como interface estável**.

- **Fase 1** — Back Office + execução controlada (RBAC + auditoria). Cron e `main.sh` continuam; toda operação manual passa pela API (SSH parametrizado contra o legado).
- **Fase 2** — Scheduler gerenciado + Executor v3 containerizado; descomissionamento do servidor em escopo.
- **Fase 3** — Event-driven, DAGs, SLAs/alertas de ausência, reprocessamento self-service com aprovação, onboarding no-code de jobs.

## Escopo: dois hosts, dois ambientes

| Servidor | Endereço | Ambiente | Escopo |
|---|---|---|---|
| `Batch-Prod-srv-sftp-2-120` (`srv-sftp-2`) | `172.17.37.120` | **PROD** (597/597 jobs) | **Em escopo** — coletado, catalogado |
| `Batch-DTU` (`com-ins-bch-mdw-dtu-1`) | `172.21.86.76` | **UAT/TEST/PROD/DEV** (540 jobs) | **Em escopo** — coletado 11/09/2026, carga pendente |
| `p-batch-1` | `172.17.37.66` | PROD | Fora do escopo |
| `reportes-130` | `172.24.6.130` | PROD | Fora do escopo |
| `P-MDW-BATCH-1`, `p-mx-batch-1`, `P-MX-SFTP-2` | — | PROD (MX) | Fora do escopo |

UAT entra **como ambiente de catálogo e de operação**, não como alvo de migração: o
descomissionamento da Fase 2 e as waves continuam sendo sobre `172.17.37.120`. A razão de trazê-lo
é o marco da Fase 1 — reprocessamento fim-a-fim pelo Back Office, com steps `upload_remote`, não
deve ser ensaiado pela primeira vez contra cliente real.

Consequências práticas:

- **O host é dimensão, não pressuposto.** `job.host` já é parte da chave natural, e `catalog load`
  / `catalog reconcile` operam por host: dois hosts convivem no mesmo catálogo sem migration.
  Reconciliar um host **nunca** pode marcar como ausente o job do outro.
- **Todo número é de um host — diga qual.** PROD: 597 linhas de job, 527 jobs distintos, 575
  contratos. UAT: 540 linhas de job, 587 contratos (coleta de 11/09/2026). Nenhum deles descreve o
  parque total da empresa, e nenhum deve ser apresentado como tal.
- `com-ins-bch-mdw-dtu-1` **existe** — é o hostname do `Batch-DTU`, o host de **UAT**. O doc de
  arquitetura original acertou o hostname e errou o papel: atribuiu a ele o parque de produção,
  que na verdade roda em `172.17.37.120`.
- **Os dois hosts não são espelhos.** UAT roda Amazon Linux 2023 e declara `America/Bogota`; PROD
  roda CentOS 7 ELS e declara `America/Lima` (ambos UTC-5 sem horário de verão, então as agendas
  coincidem hoje — mas a timezone é por agenda no catálogo, nunca global).
- Ampliar para um terceiro servidor é decisão explícita, não consequência de uma coleta nova:
  exige rodar o coletor no host, revisar o vocabulário de clientes e reavaliar as waves da Fase 2.

## Invariantes (NUNCA violar)

1. **Contrato JSON é interface estável.** Nenhuma fase reescreve os 575 contratos em massa. Extensões são aditivas e versionadas (`schema_version`). Ver `docs/contrato-json.md`.
2. **Strangler fig, não big bang.** O legado continua operando até a última wave da Fase 2. Rollback de qualquer wave = reativar entrada no crontab + pausar schedule gerenciado.
3. **Execução como dado.** Toda execução (agendada, manual, reprocesso, evento) gera registro estruturado: quem, quando, qual processo, quais steps, data-alvo, resultado, link de logs (`execution_id`).
4. **Nenhum acesso humano direto ao plano de execução.** Operação via Back Office, autenticada por Entra ID e autorizada por role. SSH é exceção break-glass auditada.
5. **Segredos em vault, nunca em arquivos ou contratos.** Contratos referenciam apenas *aliases*; o executor resolve em runtime.
6. **Observabilidade nativa, desacoplada de arquivos.** Base Grafana Cloud (Loki/Mimir) permanece; muda a fonte: logs estruturados por execução com `execution_id`, métricas emitidas pelo executor, alertas de execução *ausente* (não só de erro).
7. **Idempotência e reprocessamento como cidadãos de primeira classe.** Semântica `--dates-pattern-files` + `--manual-steps` vira API formal, com validação de data-alvo e salvaguardas contra reenvio acidental a clientes.

## Regras para desenvolvimento (Claude Code)

- **Siga a ordem do `ROADMAP.md`.** Não inicie um módulo cujas dependências bloqueantes não estejam concluídas. Etapa atual: Fase 1, iniciando por `modules/job-catalog/`.
- **Contrato da Platform API não muda entre fases.** Na Fase 1 ela traduz para SSH parametrizado; na Fase 2, enfileira no orchestrator. O Back Office nunca deve conhecer o mecanismo de execução.
- **A API nunca interpola shell arbitrário.** Invocações são construídas a partir de campos tipados (processo do catálogo + steps + data); o wrapper server-side revalida (Fase 1).
- **Catálogo é a fonte da verdade** para agendamento, RBAC (escopo domínio/cliente) e dashboards. A partir da Fase 2, crontab é artefato gerado/reconciliado, nunca editado manualmente.
- **Paridade funcional do Executor v3 com `main.sh` é obrigatória** e verificada por suíte de testes de contrato + shadow execution com diff de artefatos antes de cada cutover.
- Steps que fazem upload a clientes (`upload_remote`) exigem confirmação reforçada (Fase 1) e aprovação two-person (Fase 3).
- Ambientes (PROD/UAT/TEST/DEV) devem ser tratados como dimensão explícita em catálogo, RBAC e (Fase 3) compute isolado. **Medido nos dois hosts em 09/2026**: PROD é puro (597/597 PROD), mas o host de UAT **mistura de verdade** — UAT 378, TEST 159, **PROD 2**, DEV 1. Logo a dimensão não é só para atravessar hosts: dentro do host de UAT ela é a única coisa que separa um job de teste de um job que declara produção.
- **Ao concluir uma entrega**, marque o checkbox correspondente no `ROADMAP.md` e verifique se o marco da etapa (🏁) foi atingido antes de avançar de fase.

## Mapa do repositório

**Convenção**: `modules/<módulo>/` tem **só documentação**. O código mora em `src/<pacote>/`
(Python) ou `frontend/` (SPA) — ver os `CLAUDE.md` de `platform-api` e `back-office`, onde essa
decisão está registrada.

| Caminho | Conteúdo |
|---|---|
| `ROADMAP.md` | Ordem de execução dos módulos, dependências, checkboxes de progresso e marcos por fase |
| `docs/` | Visão geral, princípios, contrato JSON, modelo de dados, segurança, observabilidade, migração, riscos |
| `docs/adr/` | Decisões (ADR-001 e 002 fechadas em 16/09/2026; 003 e 004) e template |
| `docs/api/` | Contrato REST da Platform API (rascunho OpenAPI) |
| `modules/<módulo>/` | `CLAUDE.md` (contexto do módulo) + `SPEC.md` (requisitos e critérios de aceite); módulos com código implementado também têm `OPERACAO.md` (referência de uso) |
| `backlog/` | Backlog e critérios de aceite por fase |
| `src/catalog/` | Pacote `catalog` — modelo de dados, parser/importador do inventário e CLI (`catalog import/load/reconcile/triage/...`); doc em `modules/job-catalog/OPERACAO.md` |
| `src/platform_api/` | Pacote `platform_api` — FastAPI, OIDC/RBAC, `ExecutionBackend`; doc em `modules/platform-api/OPERACAO.md` |
| `frontend/` | SPA do Back Office (Vite + React + TypeScript); doc em `modules/back-office/OPERACAO.md` |
| `migrations/` | Alembic — schema `catalog`, incluindo o append-only de auditoria por trigger + `REVOKE` |
| `tests/` | Suíte pytest (schema de contrato, parser, banco, wrapper legado, Platform API) |
| `seed/` | Coletor (`seed/collect/`), vocabulário de curadoria (`seed/mappings/`) e pacotes coletados dos hosts (`seed/raw/`, **gitignored**: contêm nome de cliente, IP e caminho interno) |
| `docker/` + `docker-compose.yaml` | Ambiente de desenvolvimento local por profile (postgres, loki/grafana/prometheus, keycloak, minio/sftp, vault). Referência: `docker/README.md` |
| `docker/legacy/` | **Host legado simulado** — `main.sh` stub + `batch-wrapper.sh` (`command=` restrito) + fixtures. **Não é módulo da plataforma**: é o fixture contra o qual o backend SSH da Fase 1 é desenvolvido e verificado, sem tocar produção. É também o que falta de pé para o marco 🏁 da Fase 1 (reprocesso fim-a-fim) |

## Glossário rápido

- **Contrato JSON**: definição declarativa de um job em `/processes/*` (steps, `stop_on_failed`, placeholders de data).
- **`main.sh`**: engine legado que interpreta contratos. Flags relevantes: `--process-file`, `--manual-steps`, `--dates-pattern-files`, `--validate-file`, `--no-mail`.
- **Placeholders de data**: `@@@YYYY@@@`, `@@@JULIANO@@@`, `@@@YYYYMMDD@@@` etc., resolvidos em runtime.
- **Waves**: ordem de migração da Fase 2 por domínio, do menor ao maior risco: `/otros` → `/reportes` → `/saldos` + `/transacciones` → `/emisiones` + `/emboces` → `/base2` (shadow execution obrigatória).
- **Break-glass**: acesso SSH excepcional, com credencial nomeada temporária, sessão gravada e revisão posterior.