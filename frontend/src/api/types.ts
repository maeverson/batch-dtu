// Espelha `src/platform_api/schemas.py` — mesma forma, mesmos nomes de campo.
// O contrato REST não muda entre fases (docs/api/platform-api.md); manter
// este arquivo alinhado ao schemas.py é o que evita a UI inventar campo que
// a API não tem.

export type Environment = 'PROD' | 'UAT' | 'TEST' | 'DEV'
export type JobStatus = 'active' | 'disabled' | 'on_demand' | 'orphan' | 'broken'

export interface Job {
  id: string
  host: string
  process_name: string
  domain: string | null
  environment: Environment | null
  client_code: string | null
  client_name: string | null
  country_codes: string[]
  status: JobStatus
  status_reason: string | null
  kind: 'contract_job' | 'script_task'
  contract_path: string | null
  owner: string | null
  criticality: string | null
  sla: string | null
  current_contract_version_id: string | null
  updated_at: string
}

export interface JobSchedule {
  id: string
  schedule_expr: string | null
  timezone: string
  enabled: boolean
  catchup_policy: string | null
  manual_steps: string | null
  dates_pattern: string | null
  no_mail: boolean
  raw_line: string | null
}

export interface ContractStep {
  step?: number
  function: string
  stop_on_failed?: boolean
  [key: string]: unknown
}

export interface JobContract {
  version: number
  schema_version: string | null
  contract: {
    name_process?: string
    client?: string
    country?: string
    environment?: string
    description?: string
    steps: ContractStep[]
    [key: string]: unknown
  }
  contract_hash: string
  validation_status: 'valid' | 'valid_with_warnings' | 'invalid' | 'unknown'
  created_at: string
}

export interface ReconciliationFinding {
  id: string
  kind: string
  severity: 'erro' | 'aviso' | 'info'
  subject: string
  detail: string | null
  status: 'open' | 'explained' | string
  explanation: string | null
  fingerprint: string
}

export interface ReconciliationState {
  host: string
  state: 'ok' | 'divergente' | 'nunca_rodou'
  run_id: string | null
  run_finished_at: string | null
  open_findings: ReconciliationFinding[]
}

export type ChangeRequestState = 'pending' | 'applied' | 'verified' | 'cancelled' | 'expired'

export interface ChangeRequest {
  id: string
  job_id: string
  host: string
  desired_status: string
  reason: string
  state: ChangeRequestState
  marker: string
  instruction: string | null
  requested_by: string
  requested_at: string
  expires_at: string | null
  applied_by: string | null
  applied_at: string | null
  verified_at: string | null
}

export type ExecutionTrigger = 'schedule' | 'manual' | 'reprocess' | 'event' | 'dependency'
export type ExecutionStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export interface Execution {
  id: string
  job_id: string
  trigger: ExecutionTrigger
  requested_steps: string[]
  dates_pattern: string[]
  no_mail: boolean
  status: ExecutionStatus
  result: 'success' | 'failure' | 'partial' | null
  exit_code: number | null
  requested_by: string
  justification: string | null
  started_at: string | null
  ended_at: string | null
  log_link: string | null
}

export interface ExecutionRequest {
  job_id: string
  steps?: string
  dates_pattern: string[]
  confirm_target_dates: boolean
  confirm_upload_remote?: boolean
  no_mail?: boolean
  justification: string
  idempotency_key?: string
}

export interface ExecutionLogs {
  execution_id: string
  /** 'newrelic' em ambiente implantado, 'loki' no compose local. */
  backend?: string
  lines: { timestamp_ns: string; line: string }[]
}

export interface Me {
  subject: string
  display_name: string | null
  /** App roles do Entra ID presentes no token — a autorização inteira. */
  roles: string[]
  /** Ambiente que ESTA instância da API serve (um deploy por ambiente). */
  environment: string
  /** Host do catálogo que ESTA instância opera. */
  host: string
  is_admin: boolean
}

/** Corpo de `POST /admin/jobs` — `host`/`environment` vêm da instância. */
export interface AdminJobCreate {
  process_name: string
  wrapper_path?: string | null
  contract_path?: string | null
  domain?: string | null
  client_code?: string | null
  client_name?: string | null
  kind?: string
  criticality?: string | null
  sla?: string | null
  owner?: string | null
  status?: string
  status_reason?: string | null
  reason: string
}

export type AdminJobUpdate = Partial<Omit<AdminJobCreate, 'process_name' | 'reason'>> & {
  reason: string
}

export interface ApiErrorBody {
  detail?: string
}
