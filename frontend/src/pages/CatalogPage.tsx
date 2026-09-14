import { useState, type FormEvent } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useQuery } from '../api/hooks'
import { paths } from '../api/endpoints'
import type { Job } from '../api/types'
import { EnvironmentBadge, JobStatusBadge } from '../components/Badges'
import { ErrorBanner, Spinner, EmptyState } from '../components/Feedback'

const STATUS_OPTIONS = ['', 'active', 'disabled', 'on_demand', 'orphan', 'broken']
const ENV_OPTIONS = ['', 'PROD', 'UAT', 'TEST', 'DEV']

export function CatalogPage() {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const [domain, setDomain] = useState(params.get('domain') ?? '')
  const [client, setClient] = useState(params.get('client') ?? '')
  const [environment, setEnvironment] = useState(params.get('environment') ?? '')
  const [status, setStatus] = useState(params.get('status') ?? '')

  const filtros = {
    domain: params.get('domain') ?? undefined,
    client: params.get('client') ?? undefined,
    environment: params.get('environment') ?? undefined,
    status: params.get('status') ?? undefined,
  }
  const { data: jobs, error, loading } = useQuery<Job[]>(paths.jobs(filtros))

  function aplicarFiltros(e: FormEvent) {
    e.preventDefault()
    const next: Record<string, string> = {}
    if (domain) next.domain = domain
    if (client) next.client = client
    if (environment) next.environment = environment
    if (status) next.status = status
    setParams(next)
  }

  function limparFiltros() {
    setDomain('')
    setClient('')
    setEnvironment('')
    setStatus('')
    setParams({})
  }

  return (
    <div>
      <h1>Catálogo</h1>
      <p className="muted">Navegação do parque de jobs agendados — domínio, cliente, ambiente e status.</p>

      <form className="panel toolbar" onSubmit={aplicarFiltros}>
        <div className="field" style={{ marginBottom: 0 }}>
          <label htmlFor="f-domain">Domínio</label>
          <input id="f-domain" type="text" placeholder="reportes, otros…" value={domain}
                 onChange={(e) => setDomain(e.target.value)} />
        </div>
        <div className="field" style={{ marginBottom: 0 }}>
          <label htmlFor="f-client">Cliente</label>
          <input id="f-client" type="text" placeholder="código ou nome" value={client}
                 onChange={(e) => setClient(e.target.value)} />
        </div>
        <div className="field" style={{ marginBottom: 0 }}>
          <label htmlFor="f-env">Ambiente</label>
          <select id="f-env" value={environment} onChange={(e) => setEnvironment(e.target.value)}>
            {ENV_OPTIONS.map((o) => (
              <option key={o} value={o}>{o || 'Todos'}</option>
            ))}
          </select>
        </div>
        <div className="field" style={{ marginBottom: 0 }}>
          <label htmlFor="f-status">Status</label>
          <select id="f-status" value={status} onChange={(e) => setStatus(e.target.value)}>
            {STATUS_OPTIONS.map((o) => (
              <option key={o} value={o}>{o || 'Todos'}</option>
            ))}
          </select>
        </div>
        <button type="submit" className="primary">Filtrar</button>
        <button type="button" onClick={limparFiltros}>Limpar</button>
      </form>

      <ErrorBanner error={error} />
      {loading && <Spinner />}

      {!loading && jobs && jobs.length === 0 && (
        <EmptyState>
          Nenhum job visível com esses filtros — ou fora do escopo do seu <code>role_binding</code>.
        </EmptyState>
      )}

      {!loading && jobs && jobs.length > 0 && (
        <div className="panel" style={{ padding: 0, overflowX: 'auto' }}>
          <table>
            <thead>
              <tr>
                <th>Processo</th>
                <th>Domínio</th>
                <th>Cliente</th>
                <th>Ambiente</th>
                <th>Host</th>
                <th>Status</th>
                <th>Criticidade</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => (
                <tr key={job.id} className="clickable" onClick={() => navigate(`/jobs/${job.id}`)}>
                  <td className="mono">{job.process_name}</td>
                  <td>{job.domain ?? '—'}</td>
                  <td>{job.client_name ?? job.client_code ?? '—'}</td>
                  <td><EnvironmentBadge environment={job.environment} /></td>
                  <td className="faint">{job.host}</td>
                  <td><JobStatusBadge status={job.status} /></td>
                  <td className="muted">{job.criticality ?? '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {!loading && jobs && (
        <p className="faint">{jobs.length} job(s) no escopo visível.</p>
      )}
    </div>
  )
}
