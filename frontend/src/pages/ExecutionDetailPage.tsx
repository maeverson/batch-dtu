import { Link, useParams } from 'react-router-dom'
import { useQuery } from '../api/hooks'
import { paths } from '../api/endpoints'
import type { Execution, ExecutionLogs } from '../api/types'
import { ExecutionStatusBadge } from '../components/Badges'
import { ErrorBanner, Spinner } from '../components/Feedback'

const STATUS_POLL_MS = 4000
const LOGS_POLL_MS = 4000

export function ExecutionDetailPage() {
  const { executionId } = useParams<{ executionId: string }>()

  // `POST /executions` é síncrono na Fase 1 (só responde quando o SSH
  // termina — ver `api/hooks.ts`), então na prática a execução já chega aqui
  // em estado terminal; o poll aqui é para o caso de outra aba/usuário estar
  // olhando ANTES de terminar. Continuar reconsultando depois disso é
  // desperdício pequeno (uma GET a cada poucos segundos, só enquanto a aba
  // está aberta) — não vale a complexidade de desligar sozinho.
  const { data: execution, error, loading } = useQuery<Execution>(
    executionId ? paths.execution(executionId) : null,
    { pollMs: STATUS_POLL_MS },
  )

  // O proxy do Loki funciona independente do status da execution — o
  // promtail pode levar alguns segundos para embarcar o `.log` (observability,
  // Etapa 1.3), então as linhas continuam chegando um pouco depois do job
  // terminar. É esse atraso que faz valer a pena continuar reconsultando aqui
  // mesmo com a execução já em estado terminal.
  const { data: logs, error: logsError } = useQuery<ExecutionLogs>(
    executionId ? paths.executionLogs(executionId) : null,
    { pollMs: LOGS_POLL_MS },
  )

  if (loading && !execution) return <Spinner label="Carregando execução…" />
  if (error) return <ErrorBanner error={error} />
  if (!execution) return null

  return (
    <div>
      <div className="breadcrumb">
        <Link to="/execucoes">Execuções</Link> / <span className="mono">{execution.id}</span>
      </div>

      <div className="panel">
        <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
          <h1>Execução <span className="mono">{execution.id.slice(0, 8)}</span></h1>
          <ExecutionStatusBadge status={execution.status} />
        </div>
        <div className="card-grid">
          <div><div className="faint">Job</div>
            <Link to={`/jobs/${execution.job_id}`} className="mono">{execution.job_id}</Link></div>
          <div><div className="faint">Tipo</div>{execution.trigger}</div>
          <div><div className="faint">Resultado</div>{execution.result ?? '—'}</div>
          <div><div className="faint">Código de saída</div>{execution.exit_code ?? '—'}</div>
          <div><div className="faint">Solicitante</div>{execution.requested_by}</div>
          <div><div className="faint">Steps</div>{execution.requested_steps.join(', ') || 'todos'}</div>
          <div><div className="faint">Datas</div><span className="mono">{execution.dates_pattern.join(', ')}</span></div>
          <div><div className="faint">Início</div>{execution.started_at ? new Date(execution.started_at).toLocaleString() : '—'}</div>
          <div><div className="faint">Fim</div>{execution.ended_at ? new Date(execution.ended_at).toLocaleString() : '—'}</div>
        </div>
        {execution.justification && (
          <p className="muted">Justificativa: {execution.justification}</p>
        )}
        {(execution.status === 'queued' || execution.status === 'running') && (
          <p className="muted"><Spinner label="Atualizando status automaticamente…" /></p>
        )}
      </div>

      <div className="panel">
        <h2>Logs (Loki, por execution_id)</h2>
        <ErrorBanner error={logsError} />
        {!logs && !logsError && <Spinner label="Consultando Loki…" />}
        {logs && logs.lines.length === 0 && (
          <p className="muted">
            Sem linhas ainda. Se a execução acabou de rodar, aguarde alguns segundos — o agente que
            embarca o log para o Loki tem um pequeno atraso (promtail, ver <code>docker/promtail/</code>).
          </p>
        )}
        {logs && logs.lines.length > 0 && (
          <div className="log-panel">
            {logs.lines.map((l, i) => (
              <div key={i} className="log-line">{l.line}</div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
