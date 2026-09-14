# Modelo de Dados — Entidades Centrais

Banco relacional gerenciado (default: RDS PostgreSQL — ver ADR-003). Retenção e imutabilidade conforme compliance (particionamento + write locks em registros históricos).

## `job` (catálogo)

| Campo | Notas |
|---|---|
| `id`, `process_name` | Identificação |
| `domain`, `client`, `country`, `environment` | Dimensões de escopo (RBAC, dashboards) |
| `contract` (JSON, versionado) | Contrato declarativo; `schema_version` |
| `schedule`, `timezone` | Agenda; alimenta o scheduler gerenciado |
| `status` + `reason` | ativo/desabilitado com motivo registrado (substitui comentários do crontab) |
| `criticality`, `sla`, `owner` | Base para alertas de atraso/ausência (Fase 3) |

Seed inicial = inventário consolidado (597 linhas de job → 527 jobs distintos). **Substitui o crontab como fonte da verdade.**

## `execution`

| Campo | Notas |
|---|---|
| `id`, `job_id` | — |
| `trigger` | `schedule` \| `manual` \| `reprocess` \| `event` \| `dependency` |
| `requested_steps` | Subconjunto de steps (semântica `--manual-steps`) |
| `dates_pattern` | Data(s)-alvo |
| `started_at`, `ended_at`, `result`, `worker` | — |
| `log_link` | `execution_id` no Loki |

Um registro por invocação — **inclusive execuções do legado disparadas via API na Fase 1**.

## `crontab_change_request`

| Campo | Notas |
|---|---|
| `id`, `job_id`, `host` | — |
| `desired_status` | `active` \| `disabled` |
| `reason`, `requested_by`, `requested_at`, `expires_at` | Motivo canônico; o crontab guarda só a âncora |
| `instruction` | A linha-alvo a aplicar, não um diff |
| `marker` | `#BO:<job_id>:<change_id>` — âncora de parsing da reconciliação |
| `state` | `pending` → `applied` → `verified`; `cancelled`, `expired` |
| `applied_by`, `applied_at` | Opcionais: o operador pode não informar |
| `verified_at`, `verified_snapshot_id` | Preenchidos pela **reconciliação**, por detecção |

Separa divergência **esperada** (mudança em andamento) de **drift não gerenciado** (alguém editou
o crontab por fora). Sem esta entidade, a reconciliação só sabe dizer `catálogo ≠ crontab`.

## `role_binding`

| Campo | Notas |
|---|---|
| `subject`, `subject_type` | Sujeito do Entra ID: `user` ou `group` |
| `role` | `batch.viewer` \| `batch.operator` \| `batch.operator-prod` \| `batch.admin` |
| `scope_domain`, `scope_environment`, `scope_host` | **`NULL` = todas**. Conceder escopo restringe |
| `granted_by`, `revoked_at`, `revoked_by` | Revogação é soft: a trilha permanece |

O escopo mora no catálogo, e não em grupos do Entra, porque a pergunta operacional — "quem pode
executar este job" — se responde por domínio/ambiente/host do próprio job.

## `audit_event`

| Campo | Notas |
|---|---|
| `id`, `actor` (Entra ID), `action`, `target`, `payload`, `timestamp`, `source` | Imutável (append-only). Cobre também mudanças de catálogo e RBAC. |

## `approval` (Fase 3)

| Campo | Notas |
|---|---|
| `id`, `execution_request_id`, `requester`, `approver`, `status`, `justification` | Workflow de reprocessamento sensível (two-person rule para steps de upload a cliente) |

## `connection_alias`

| Campo | Notas |
|---|---|
| `name`, `type`, `vault_reference`, `owners`, `validity` | **Somente metadados** — segredo real fica no vault. `validity` monitora chaves/certificados/GPG. |
