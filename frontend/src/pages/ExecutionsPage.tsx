import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery } from '../api/hooks'
import { paths } from '../api/endpoints'
import type { Execution } from '../api/types'
import { ExecutionStatusBadge } from '../components/Badges'
import { ErrorBanner, Spinner, EmptyState } from '../components/Feedback'

const POLL_MS = 5000

export function ExecutionsPage() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const jobId = params.get('job_id') ?? undefined
  const [autoRefresh, setAutoRefresh] = useState(true)

  const { data: executions, error, loading, reload } = useQuery<Execution[]>(
    paths.executions({ job_id: jobId }),
    { pollMs: autoRefresh ? POLL_MS : undefined },
  )

  return (
    <div>
      <h1>Execuções</h1>
      <p className="muted">
        Execuções manuais, reprocessos e agendadas — monitoramento por reconsulta
        {jobId && <> filtradas por job <span className="mono">{jobId}</span></>}.
      </p>

      <div className="toolbar">
        <label>
          <input type="checkbox" checked={autoRefresh} onChange={(e) => setAutoRefresh(e.target.checked)} />
          {' '}atualizar a cada {POLL_MS / 1000}s
        </label>
        <button onClick={reload}>Atualizar agora</button>
      </div>

      <ErrorBanner error={error} />
      {loading && !executions && <Spinner />}

      {executions && executions.length === 0 && (
        <EmptyState>Nenhuma execução visível ainda.</EmptyState>
      )}

      {executions && executions.length > 0 && (
        <div className="panel" style={{ padding: 0, overflowX: 'auto' }}>
          <table>
            <thead>
              <tr>
                <th>Início</th><th>Tipo</th><th>Status</th><th>Resultado</th>
                <th>Solicitante</th><th>Datas</th>
              </tr>
            </thead>
            <tbody>
              {executions.map((ex) => (
                <tr key={ex.id} className="clickable" onClick={() => navigate(`/execucoes/${ex.id}`)}>
                  <td>{ex.started_at ? new Date(ex.started_at).toLocaleString() : '—'}</td>
                  <td>{ex.trigger}</td>
                  <td><ExecutionStatusBadge status={ex.status} /></td>
                  <td className="muted">{ex.result ?? '—'}</td>
                  <td>{ex.requested_by}</td>
                  <td className="mono">{ex.dates_pattern.join(', ')}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
