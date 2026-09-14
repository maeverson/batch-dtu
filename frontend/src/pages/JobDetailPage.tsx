import { useState, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useMe, useQuery } from '../api/hooks'
import { paths } from '../api/endpoints'
import type { ChangeRequest, Execution, Job, JobContract, JobSchedule, ReconciliationState } from '../api/types'
import {
  EnvironmentBadge,
  ExecutionStatusBadge,
  JobStatusBadge,
  ReconciliationBadge,
  SeverityBadge,
} from '../components/Badges'
import { ErrorBanner, Spinner } from '../components/Feedback'
import { ExecuteModal } from '../components/ExecuteModal'
import { StatusChangeModal } from '../components/StatusChangeModal'
import { canAttemptOperate } from '../rbac'

export function JobDetailPage() {
  const { jobId } = useParams<{ jobId: string }>()
  const navigate = useNavigate()
  const { data: me } = useMe()

  const { data: job, error: jobError, loading: jobLoading, reload: reloadJob } =
    useQuery<Job>(jobId ? paths.job(jobId) : null)
  const { data: schedules } = useQuery<JobSchedule[]>(jobId ? paths.jobSchedules(jobId) : null)
  const { data: contract, error: contractError } =
    useQuery<JobContract>(jobId ? paths.jobContract(jobId) : null)
  const { data: reconciliation, reload: reloadReconciliation } =
    useQuery<ReconciliationState>(jobId ? paths.jobReconciliation(jobId) : null)
  const { data: executions } = useQuery<Execution[]>(
    jobId ? paths.executions({ job_id: jobId }) : null,
  )

  const [showExecute, setShowExecute] = useState(false)
  const [showStatusChange, setShowStatusChange] = useState(false)
  const [lastChangeRequest, setLastChangeRequest] = useState<ChangeRequest>()

  if (jobLoading && !job) return <Spinner label="Carregando job…" />
  if (jobError) return <ErrorBanner error={jobError} />
  if (!job) return null

  const podeOperar = canAttemptOperate(me, job)

  return (
    <div>
      <div className="breadcrumb">
        <Link to="/catalogo">Catálogo</Link> / {job.process_name}
      </div>

      <div className="panel">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 12 }}>
          <div>
            <h1 className="mono">{job.process_name}</h1>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 4 }}>
              <JobStatusBadge status={job.status} />
              <EnvironmentBadge environment={job.environment} />
              <span className="badge neutral">{job.domain ?? 'sem domínio'}</span>
              {reconciliation && <ReconciliationBadge state={reconciliation.state} />}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              className="primary"
              disabled={!podeOperar}
              title={!podeOperar ? 'Sua role/escopo não cobre operar este job' : undefined}
              onClick={() => setShowExecute(true)}
            >
              Executar / reprocessar
            </button>
            <button
              disabled={!podeOperar}
              title={!podeOperar ? 'Sua role/escopo não cobre operar este job' : undefined}
              onClick={() => setShowStatusChange(true)}
            >
              {job.status === 'active' ? 'Desabilitar' : 'Reabilitar'}
            </button>
          </div>
        </div>

        {job.status_reason && <p className="muted" style={{ marginTop: 8 }}>Motivo do status: {job.status_reason}</p>}

        <div className="card-grid" style={{ marginTop: 16 }}>
          <Field label="Host">{job.host}</Field>
          <Field label="Cliente">{job.client_name ?? job.client_code ?? '—'}</Field>
          <Field label="Owner">{job.owner ?? '—'}</Field>
          <Field label="Criticidade">{job.criticality ?? '—'}</Field>
          <Field label="SLA">{job.sla ?? '—'}</Field>
          <Field label="Contrato">{job.contract_path ?? '—'}</Field>
        </div>
      </div>

      {lastChangeRequest && (
        <div className="panel">
          <h2>Mudança de agendamento aberta</h2>
          <p className="muted">
            Estado <strong>{lastChangeRequest.state}</strong> — instrução a aplicar manualmente
            (Fase 1 não escreve no crontab):
          </p>
          <pre className="json-viewer">{lastChangeRequest.instruction}</pre>
        </div>
      )}

      <div className="panel">
        <h2>Reconciliação</h2>
        {reconciliation ? (
          <>
            <p className="muted">
              Host <span className="mono">{reconciliation.host}</span> — última rodada{' '}
              {reconciliation.run_finished_at ? new Date(reconciliation.run_finished_at).toLocaleString() : '—'}
              {' '}<button className="link" onClick={reloadReconciliation}>atualizar</button>
            </p>
            {reconciliation.open_findings.length > 0 ? (
              <table>
                <thead>
                  <tr><th>Severidade</th><th>Tipo</th><th>Assunto</th><th>Detalhe</th></tr>
                </thead>
                <tbody>
                  {reconciliation.open_findings.map((f) => (
                    <tr key={f.id}>
                      <td><SeverityBadge severity={f.severity} /></td>
                      <td className="mono">{f.kind}</td>
                      <td>{f.subject}</td>
                      <td className="muted">{f.detail ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="muted">Sem divergência aberta para este job.</p>
            )}
          </>
        ) : <Spinner />}
      </div>

      <div className="panel">
        <h2>Agenda</h2>
        {schedules && schedules.length > 0 ? (
          <table>
            <thead>
              <tr><th>Expressão cron</th><th>Timezone</th><th>Habilitada</th><th>Linha original</th></tr>
            </thead>
            <tbody>
              {schedules.map((s) => (
                <tr key={s.id}>
                  <td className="mono">{s.schedule_expr ?? 'on-demand'}</td>
                  <td>{s.timezone}</td>
                  <td>{s.enabled ? 'sim' : 'não'}</td>
                  <td className="mono faint">{s.raw_line ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <p className="muted">Sem agenda registrada (on-demand).</p>}
      </div>

      <div className="panel">
        <h2>Contrato JSON</h2>
        {contractError && <ErrorBanner error={contractError} />}
        {contract && (
          <>
            <p className="muted">
              Versão {contract.version} — validação: <strong>{contract.validation_status}</strong>
            </p>
            <pre className="json-viewer">{JSON.stringify(contract.contract, null, 2)}</pre>
          </>
        )}
      </div>

      <div className="panel">
        <h2>Histórico de execuções</h2>
        {executions && executions.length > 0 ? (
          <table>
            <thead>
              <tr><th>Início</th><th>Tipo</th><th>Status</th><th>Solicitante</th><th>Datas</th></tr>
            </thead>
            <tbody>
              {executions.map((ex) => (
                <tr key={ex.id} className="clickable" onClick={() => navigate(`/execucoes/${ex.id}`)}>
                  <td>{ex.started_at ? new Date(ex.started_at).toLocaleString() : '—'}</td>
                  <td>{ex.trigger}</td>
                  <td><ExecutionStatusBadge status={ex.status} /></td>
                  <td>{ex.requested_by}</td>
                  <td className="mono">{ex.dates_pattern.join(', ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : <p className="muted">Nenhuma execução registrada para este job ainda.</p>}
      </div>

      {showExecute && (
        <ExecuteModal
          job={job}
          contract={contract}
          onClose={() => setShowExecute(false)}
          onExecuted={(ex) => {
            setShowExecute(false)
            navigate(`/execucoes/${ex.id}`)
          }}
        />
      )}

      {showStatusChange && (
        <StatusChangeModal
          job={job}
          onClose={() => setShowStatusChange(false)}
          onChanged={(cr) => {
            setShowStatusChange(false)
            setLastChangeRequest(cr)
            reloadJob()
          }}
        />
      )}
    </div>
  )
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <div className="faint" style={{ fontSize: 11, textTransform: 'uppercase' }}>{label}</div>
      <div>{children}</div>
    </div>
  )
}
