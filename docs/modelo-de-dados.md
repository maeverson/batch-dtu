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
