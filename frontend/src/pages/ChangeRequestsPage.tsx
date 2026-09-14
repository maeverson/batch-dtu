import { useState } from 'react'
import { Link } from 'react-router-dom'
import { apiPost, ApiError } from '../api/client'
import { paths } from '../api/endpoints'
import { useMe, useQuery, useToken } from '../api/hooks'
import type { ChangeRequest } from '../api/types'
import { ChangeRequestStateBadge } from '../components/Badges'
import { ErrorBanner, Spinner, EmptyState } from '../components/Feedback'
import { isViewerOnly } from '../rbac'

const STATE_OPTIONS = ['', 'pending', 'applied', 'verified', 'cancelled', 'expired']

export function ChangeRequestsPage() {
  const token = useToken()
  const { data: me } = useMe()
  const [state, setState] = useState('pending')
  const { data: crs, error, loading, reload } = useQuery<ChangeRequest[]>(
    paths.changeRequests({ state: state || undefined }),
  )
  const [cancelling, setCancelling] = useState<string>()

  async function cancelar(cr: ChangeRequest) {
    const motivo = window.prompt(`Motivo para desistir da mudança de ${cr.job_id}:`)
    if (!motivo || motivo.trim().length < 3) return
    setCancelling(cr.id)
    try {
      await apiPost(paths.changeRequestCancel(cr.id), token, { reason: motivo })
      reload()
    } catch (e) {
      window.alert(e instanceof ApiError ? e.message : String(e))
    } finally {
      setCancelling(undefined)
    }
  }

  return (
    <div>
      <h1>Mudanças de agendamento</h1>
      <p className="muted">
        Worklist do ciclo <code>pending → applied → verified</code>. A aplicação da linha-alvo no
        crontab continua manual na Fase 1 — a verificação é automática, pela reconciliação.
      </p>

      <div className="toolbar">
        <label>
          Estado{' '}
          <select value={state} onChange={(e) => setState(e.target.value)}>
            {STATE_OPTIONS.map((s) => <option key={s} value={s}>{s || 'Todos'}</option>)}
          </select>
        </label>
        <button onClick={reload}>Atualizar</button>
      </div>

      <ErrorBanner error={error} />
      {loading && <Spinner />}
      {crs && crs.length === 0 && <EmptyState>Nenhuma mudança de agendamento nesse estado.</EmptyState>}

      {crs && crs.length > 0 && (
        <div className="panel" style={{ padding: 0, overflowX: 'auto' }}>
          <table>
            <thead>
              <tr><th>Job</th><th>Estado</th><th>Desejado</th><th>Motivo</th><th>Solicitado por</th><th>Expira</th><th></th></tr>
            </thead>
            <tbody>
              {crs.map((cr) => (
                <tr key={cr.id}>
                  <td><Link to={`/jobs/${cr.job_id}`} className="mono">{cr.job_id.slice(0, 8)}</Link></td>
                  <td><ChangeRequestStateBadge state={cr.state} /></td>
                  <td>{cr.desired_status}</td>
                  <td className="muted">{cr.reason}</td>
                  <td>{cr.requested_by}</td>
                  <td className="faint">{cr.expires_at ? new Date(cr.expires_at).toLocaleString() : '—'}</td>
                  <td>
                    {cr.state === 'pending' && !isViewerOnly(me) && (
                      <button disabled={cancelling === cr.id} onClick={() => cancelar(cr)}>
                        Desistir
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
