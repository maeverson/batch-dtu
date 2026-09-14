# ROADMAP — Ordem de Execução dos Módulos

Plano incremental de desenvolvimento. Cada etapa entrega valor verificável e desbloqueia a seguinte. Marque os checkboxes conforme concluir; os critérios de aceite detalhados estão no `SPEC.md` de cada módulo e em `backlog/fase-N.md`.

**Regra geral**: um módulo só inicia quando suas dependências marcadas como *bloqueantes* estiverem concluídas. Itens em paralelo podem avançar simultaneamente.

---

## Fase 1 — Back Office + Execução Controlada

### Etapa 1.1 — `job-catalog` ✅ EM ANDAMENTO (ponto de partida)

Fundação de tudo: catálogo como fonte da verdade.

- [ ] Modelo de dados (`job`, `execution`, `audit_event`, `connection_alias`) — ver `docs/modelo-de-dados.md`
  - [x] `job` (+ `job_schedule`, `job_contract_version`, `job_revision`, `job_connection_alias`), `audit_event`, `connection_alias`, `crontab_snapshot`/`crontab_entry`, `reconciliation_run`/`reconciliation_finding`
  - [ ] `execution` — sem fonte na Fase 1 enquanto o cron executa; nasce com o `ExecutionBackend` da Etapa 1.2
- [x] Migrations e banco (ADR-003: default RDS PostgreSQL) — Alembic; append-only de auditoria por trigger + REVOKE, com teste de drift modelo × migration
- [x] Importador/seed do inventário, com `status_reason` dos comentários do crontab — `catalog import` (relatório) e `catalog load` (persistência idempotente).
  **A coleta de 09/2026 mediu 597 linhas de job (393 ativas, 204 desabilitadas), consolidadas em 527 jobs distintos**, não as 509 entradas da premissa inicial. O crontab tem 1352 linhas ao todo: 597 de job, 317 de manutenção, o resto prosa, `VAR=` e branco
- [x] Validação de schema do contrato JSON na escrita — `src/catalog/contract_schema.py`, veredito gravado em `job_contract_version.validation_status`; `catalog validate` valida em lote
- [x] Versionamento de contrato e metadados — `job_contract_version` (append-only, por hash) e `job_revision` (snapshot + diff do que mudou). Consulta hoje é SQL; endpoint vem na Etapa 1.2
- [x] Job de reconciliação crontab × catálogo (diff report) — `catalog reconcile` (leitura pura, grava `reconciliation_run`) + `catalog explain` para fechar divergência conhecida
- [x] **Coleta, import e carga do host de UAT** (`Batch-DTU` / `com-ins-bch-mdw-dtu-1`, `172.21.86.76`)
  - [x] Coleta (11/09/2026) e `catalog import` — 540 linhas de job, 587 contratos, **21 erros / 22 avisos**. Relatório em `seed/raw/reports/import-com-ins-bch-mdw-dtu-1.md`
  - [x] Curadoria de cliente — **540/540 jobs curados**. Nomes derivados do campo `client` dos contratos e corroborados nos dois hosts (PROD e UAT são clones de homologação), não adivinhados. Resta `mas` em PROD (1 job), sem `client` em nenhum contrato: só o owner resolve
  - [x] Códigos legitimamente compartilhados declarados em `shared_client_codes` (`bnp`, `tup`, `coo`) — repetem-se nos dois clones na mesma proporção, logo são convenção e não erro de contrato
  - [x] `catalog load` do host de UAT — 519 jobs, 540 agendas, 507 versões de contrato, 833 linhas de crontab, 1031 `audit_event`

**Entrega verificável**:

- [x] Inventário consultável via banco — **1046 jobs** dos dois hosts (`srv-sftp-2` 527 PROD;
  `com-ins-bch-mdw-dtu-1` 365 UAT + 151 TEST + 2 PROD + 1 DEV), 1137 agendas, 1025 versões de
  contrato, 2185 linhas de crontab preservadas, 2859 eventos de auditoria. Reconciliação bate
  1:1 nos dois hosts, com zero divergência estrutural.
- [ ] Reconciliação sem divergências não explicadas — **27 erros abertos** (PROD 6, UAT 21).
  Dependem de decisão de owner; ver `seed/raw/reports/triagem-owners.md` e feche cada um com
  `catalog explain`.
**Desbloqueia**: `platform-api` (bloqueante — a API lê/escreve o catálogo).

### Etapa 1.2 — `platform-api`

- [ ] Autenticação Entra ID (OIDC) + RBAC (`batch.viewer/operator/operator-prod/admin`) com escopo domínio/ambiente — com UAT no catálogo, `operator-prod` passa a ter contraparte real e a distinção deixa de ser teórica
- [ ] Interface `ExecutionBackend` + implementação Fase 1 (SSH parametrizado via `backoffice_svc` + wrapper `command=`)
- [ ] Endpoints de catálogo (busca/filtro, validação, enable/disable com reason)
- [ ] Endpoint de execução manual (steps, dates_pattern, pré-validação, confirmação de data-alvo)
- [ ] Reprocesso multi-data com serialização + lock por processo
- [ ] Auditoria automática (`audit_event`) em toda ação de escrita

**Entrega verificável**: execução manual de um job de teste em UAT via `curl`, com registro de auditoria.
**Desbloqueia**: `back-office` (bloqueante) e `observability` 1.3 (parcial — pode iniciar em paralelo assim que `execution_id` existir).

### Etapa 1.3 — `observability` (entregas da Fase 1) — *paralelo com 1.4*

- [ ] Injeção de `execution_id` no nome dos arquivos de log
- [ ] Link auditoria → stream Loki por `execution_id`
- [ ] Painel de execuções manuais (Grafana)

### Etapa 1.4 — `back-office` — *paralelo com 1.3*

- [ ] Navegação do catálogo (filtros por domínio/cliente/ambiente/status)
- [ ] Fluxo de execução manual com confirmação explícita de data-alvo
- [ ] Confirmação reforçada para `upload_remote` em PROD (redigitar data)
- [ ] Reprocesso multi-data com preview da serialização
- [ ] Monitoramento em tempo real (status + logs via API)
- [ ] Enable/disable com reason + estado da reconciliação
- [ ] Renderização condicionada a role

### 🏁 Marco de conclusão da Fase 1

- [ ] Reprocessamento **Zinli/MFTech executado fim-a-fim via Back Office** — **ensaiado antes em UAT** (`172.21.86.76`): steps `upload_remote` não estreiam contra cliente real
- [ ] Zero execuções manuais via SSH fora do break-glass (auditoria sshd × plataforma)
- [ ] 100% das execuções manuais com registro de auditoria

---

## Fase 2 — Scheduler Gerenciado + Executor v3 + Descomissionamento

> Pré-requisito: decisões ADR-001 (engine de orquestração) e ADR-002 (runtime do executor) fechadas no kick-off.

### Etapa 2.1 — `secrets-migration` — *paralelo com 2.2*

- [ ] Migrador alias-a-alias `connections.json` → vault, com verificação
- [ ] Dual-read + checagem de consistência agendada (evolução do `validate_connections_json.sh`)
- [ ] Metadados em `connection_alias` (vault_reference, owners, validity)
- [ ] Monitoramento de validade de chaves GPG de cliente + alerta proativo

### Etapa 2.2 — `executor-v3` — *paralelo com 2.1*

- [ ] **Suíte de testes de contrato primeiro** (padrões Base2 `stop_on_failed=true` e Sodexo 78 steps) — rodando contra `main.sh` como baseline
- [ ] Conectores: SFTP, S3, Azure, GPG, copy_local, send_mail (least privilege por alias)
- [ ] Placeholders de data e semântica `stop_on_failed`
- [ ] `execute_command` restrito a hosts/scripts registrados (conforme ADR-004)
- [ ] Resolução de aliases via vault (depende da 2.1 pelo menos em dual-read)
- [ ] Logs estruturados JSON + métricas com `execution_id`
- [ ] Empacotamento em container efêmero (ECS Fargate ou equivalente do ADR-001)

**Entrega verificável**: suíte de contrato verde contra os dois executores.

### Etapa 2.3 — `orchestrator`

Depende de: executor-v3 funcional (2.2).

- [ ] Fila de execução com prioridade por criticidade
- [ ] Serialização declarativa por destino SFTP / cliente / processo
- [ ] Retries com backoff + timeouts por execução e step
- [ ] Registro completo de ciclo de vida em `execution`
- [ ] Segunda implementação de `ExecutionBackend` na Platform API (enfileirar no orchestrator) — **sem mudar o contrato REST**

### Etapa 2.4 — `scheduler`

Depende de: orchestrator (2.3) — o scheduler só dispara, não executa.

- [ ] Sync catálogo → schedules gerenciados (timezone explícita, janela, catch-up policy)
- [ ] Importador das agendas do crontab via catálogo
- [ ] Pausar/retomar por schedule (mecânica de rollback)
- [ ] Drift detection catálogo × schedules

### Etapa 2.5 — Migração por waves

Sequência fixa (menor → maior risco), com critérios de cutover por job em `docs/migracao-e-compatibilidade.md`:

- [ ] Wave 1: `/otros`
- [ ] Wave 2: `/reportes`
- [ ] Wave 3: `/saldos` + `/transacciones`
- [ ] Wave 4: `/emisiones` + `/emboces` (notificação prévia a clientes: IP de egress)
- [ ] Wave 5: `/base2` — **dual shadow execution obrigatória** + jobs com `custom_scripts/` (pré-requisito: inventário de scripts)

### 🏁 Marco de conclusão da Fase 2

- [ ] 100% dos jobs ativos agendados fora do crontab
- [ ] Execução reproduzível de qualquer worker
- [ ] Host `Batch-Prod-srv-sftp-2-120` (`172.17.37.120`) desligado ou reduzido a jump host fino
- [ ] RTO validado em drill de disaster recovery

---

## Fase 3 — Evolução Event-Driven

Ordem sugerida por dependência técnica e valor:

### Etapa 3.1 — `observability` (SLAs e ausência)

- [ ] SLO por job derivado do catálogo
- [ ] Alertas de atraso e execução ausente
- [ ] Dashboard diário realizado × esperado

### Etapa 3.2 — `orchestrator` (DAGs) + `scheduler` (eventos)

- [ ] Dependências explícitas entre jobs (cadeias `parte1` → `parte2`)
- [ ] Triggers por evento: S3 nativo e watchers SFTP (substitui polling em `/reportes` e `/base2`)

### Etapa 3.3 — `platform-api` + `back-office` (aprovações)

- [ ] Entidade `approval` + endpoints (two-person rule, aprovador ≠ solicitante)
- [ ] Fila de aprovações na UI; SOP Zinli vira workflow governado

### Etapa 3.4 — `back-office` (onboarding no-code)

- [ ] Wizard por arquétipos (fechamento, MFT, relatórios) gerando contrato validado por schema
- [ ] Promoção versionada UAT → PROD
- [ ] `--validate-file` como validação contínua do catálogo

### Etapa 3.5 — Multi-tenancy, FinOps e resiliência

- [ ] Limites declarativos de concorrência por destino/cliente
- [ ] Isolamento físico de compute por ambiente (PROD/UAT/TEST)
- [ ] Right-sizing por job (fim do heap fixo de 2 GB) + scale-to-zero
- [ ] Plano de execução recriável via IaC (DR multi-região/AZ)

---

## Visão resumida de dependências

```
job-catalog ──► platform-api ──► back-office
                     │
                     └──► observability (F1)

secrets-migration ──┐
                    ├──► executor-v3 ──► orchestrator ──► scheduler ──► waves 1..5
suíte de contrato ──┘

waves concluídas ──► Fase 3 (SLAs → DAGs/eventos → aprovações → no-code → FinOps)
```