import type { ChangeRequestState, ExecutionStatus, JobStatus } from '../api/types'

const JOB_STATUS: Record<JobStatus, { label: string; tone: string }> = {
  active: { label: 'Ativo', tone: 'ok' },
  disabled: { label: 'Desabilitado', tone: 'neutral' },
  on_demand: { label: 'On-demand', tone: 'info' },
  orphan: { label: 'Órfão', tone: 'warn' },
  broken: { label: 'Quebrado', tone: 'error' },
}

export function JobStatusBadge({ status }: { status: JobStatus }) {
  const cfg = JOB_STATUS[status] ?? { label: status, tone: 'neutral' }
  return <span className={`badge ${cfg.tone}`}>{cfg.label}</span>
}

const EXECUTION_STATUS: Record<ExecutionStatus, { label: string; tone: string }> = {
  queued: { label: 'Na fila', tone: 'neutral' },
  running: { label: 'Em execução', tone: 'info' },
  succeeded: { label: 'Sucesso', tone: 'ok' },
  failed: { label: 'Falhou', tone: 'error' },
  cancelled: { label: 'Cancelada', tone: 'neutral' },
}

export function ExecutionStatusBadge({ status }: { status: ExecutionStatus }) {
  const cfg = EXECUTION_STATUS[status] ?? { label: status, tone: 'neutral' }
  return <span className={`badge ${cfg.tone}`}>{cfg.label}</span>
}

const CHANGE_REQUEST_STATE: Record<ChangeRequestState, { label: string; tone: string }> = {
  pending: { label: 'Pendente', tone: 'warn' },
  applied: { label: 'Aplicada', tone: 'info' },
  verified: { label: 'Verificada', tone: 'ok' },
  cancelled: { label: 'Cancelada', tone: 'neutral' },
  expired: { label: 'Expirada', tone: 'error' },
}

export function ChangeRequestStateBadge({ state }: { state: ChangeRequestState }) {
  const cfg = CHANGE_REQUEST_STATE[state] ?? { label: state, tone: 'neutral' }
  return <span className={`badge ${cfg.tone}`}>{cfg.label}</span>
}

const SEVERITY: Record<string, string> = { erro: 'error', aviso: 'warn', info: 'info' }

export function SeverityBadge({ severity }: { severity: string }) {
  return <span className={`badge ${SEVERITY[severity] ?? 'neutral'}`}>{severity}</span>
}

const RECONCILIATION: Record<string, { label: string; tone: string }> = {
  ok: { label: 'Reconciliado', tone: 'ok' },
  divergente: { label: 'Divergente', tone: 'error' },
  nunca_rodou: { label: 'Sem reconciliação', tone: 'neutral' },
}

export function ReconciliationBadge({ state }: { state: string }) {
  const cfg = RECONCILIATION[state] ?? { label: state, tone: 'neutral' }
  return <span className={`badge ${cfg.tone}`}>{cfg.label}</span>
}

export function EnvironmentBadge({ environment }: { environment: string | null }) {
  if (!environment) return <span className="badge neutral">—</span>
  const tone = environment === 'PROD' ? 'error' : environment === 'UAT' ? 'warn' : 'info'
  return <span className={`badge ${tone}`}>{environment}</span>
}
