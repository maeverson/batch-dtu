import { useMemo, useState, type FormEvent } from 'react'
import { useQuery, useToken } from '../api/hooks'
import { paths } from '../api/endpoints'
import { apiDelete, apiPatch, apiPost, ApiError } from '../api/client'
import type { AdminJobCreate, Job, JobStatus } from '../api/types'
import { JobStatusBadge } from '../components/Badges'
import { ErrorBanner, Spinner, EmptyState } from '../components/Feedback'
import { isAdmin } from '../rbac'
import { useMe } from '../api/hooks'

const STATUS: JobStatus[] = ['active', 'disabled', 'on_demand', 'orphan', 'broken']

/**
 * CRUD de administração do catálogo — só `batch.admin` (`/admin/*` na
 * Platform API). A rota já é fechada em `App.tsx`; este gate aqui existe
 * porque a URL pode ser digitada à mão, e o servidor devolveria 403 sem
 * explicar nada útil ao operador.
 *
 * Três coisas que a tela deixa explícitas de propósito, porque são as que
 * geram incidente quando ficam implícitas:
 *
 * - **Ambiente e host não são campos.** Vêm da instância (`GET /me`); um job
 *   criado aqui nasce no ambiente que esta API serve.
 * - **Motivo é obrigatório** em toda escrita — é o que vai para `audit_event`.
 * - **Desativar ≠ parar de rodar.** Enquanto a entrada existir no crontab, o
 *   job continua executando no host; parar de verdade é o fluxo de mudança de
 *   agenda (`PATCH /jobs/{id}/status`, aba "Mudanças de agenda").
 */
export function AdminPage() {
  const token = useToken()
  const { data: me } = useMe()
  const [busca, setBusca] = useState('')
  const [filtro, setFiltro] = useState('')
  const [erro, setErro] = useState<string>()
  const [aviso, setAviso] = useState<string>()
  const [editando, setEditando] = useState<Job | null>(null)
  const [criando, setCriando] = useState(false)

  const caminho = useMemo(() => paths.adminJobs({ q: filtro || undefined }), [filtro])
  const { data: jobs, error, loading, reload } = useQuery<Job[]>(isAdmin(me) ? caminho : null)

  if (me && !isAdmin(me)) {
    return (
      <div>
        <h1>Administração</h1>
        <ErrorBanner error="Esta área exige a role batch.admin no Entra ID." />
      </div>
    )
  }

  async function executar(acao: () => Promise<unknown>, mensagem: string) {
    setErro(undefined)
    setAviso(undefined)
    try {
      await acao()
      setAviso(mensagem)
      setEditando(null)
      setCriando(false)
      reload()
    } catch (e) {
      setErro(e instanceof ApiError ? e.message : String(e))
    }
  }

  async function desativar(job: Job) {
    const motivo = window.prompt(
      `Desativar "${job.process_name}" no catálogo.\n\n` +
        'Atenção: isto NÃO remove a entrada do crontab — o job continua rodando no host ' +
        'até a mudança de agenda ser aplicada.\n\nMotivo (vai para a auditoria):',
    )
    if (!motivo) return
    await executar(
      () => apiDelete(paths.adminJob(job.id), token, { reason: motivo }),
      `"${job.process_name}" desativado no catálogo.`,
    )
  }

  return (
    <div>
      <h1>Administração</h1>
      <p className="muted">
        CRUD do catálogo para <strong>batch.admin</strong>. Esta instância administra{' '}
        <strong>{me?.environment}</strong> no host <strong>{me?.host}</strong> — jobs de outro
        ambiente são administrados pela instância daquele ambiente.
      </p>

      {erro && <ErrorBanner error={erro} />}
      {aviso && <div className="panel">{aviso}</div>}

      <form
        className="panel toolbar"
        onSubmit={(e: FormEvent) => {
          e.preventDefault()
          setFiltro(busca)
        }}
      >
        <div className="field" style={{ marginBottom: 0 }}>
          <label htmlFor="a-busca">Processo</label>
          <input
            id="a-busca"
            type="text"
            placeholder="parte do nome"
            value={busca}
            onChange={(e) => setBusca(e.target.value)}
          />
        </div>
        <button type="submit" className="primary">
          Buscar
        </button>
        <button type="button" onClick={() => setCriando(true)}>
          Novo job
        </button>
      </form>

      {criando && (
        <JobForm
          titulo="Novo job"
          onCancel={() => setCriando(false)}
          onSubmit={(corpo) =>
            executar(
              () => apiPost(paths.adminJobs({}), token, corpo),
              `Job "${corpo.process_name}" criado.`,
            )
          }
        />
      )}

      {editando && (
        <JobForm
          titulo={`Editar ${editando.process_name}`}
          job={editando}
          onCancel={() => setEditando(null)}
          onSubmit={(corpo) =>
            executar(
              () => apiPatch(paths.adminJob(editando.id), token, corpo),
              `Job "${editando.process_name}" atualizado.`,
            )
          }
        />
      )}

      {loading && <Spinner />}
      {error && <ErrorBanner error={error} />}
      {jobs && jobs.length === 0 && <EmptyState>Nenhum job neste filtro.</EmptyState>}

      {jobs && jobs.length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Processo</th>
              <th>Domínio</th>
              <th>Cliente</th>
              <th>Owner</th>
              <th>Status</th>
              <th>Ações</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.id}>
                <td>
                  <code>{job.process_name}</code>
                </td>
                <td>{job.domain ?? '—'}</td>
                <td>{job.client_name ?? job.client_code ?? '—'}</td>
                <td>{job.owner ?? '—'}</td>
                <td>
                  <JobStatusBadge status={job.status} />
                </td>
                <td>
                  <button type="button" onClick={() => setEditando(job)}>
                    Editar
                  </button>{' '}
                  <button
                    type="button"
                    className="danger"
                    disabled={job.status === 'disabled'}
                    onClick={() => desativar(job)}
                  >
                    Desativar
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

function JobForm({
  titulo,
  job,
  onCancel,
  onSubmit,
}: {
  titulo: string
  job?: Job
  onCancel: () => void
  onSubmit: (corpo: AdminJobCreate) => void
}) {
  const [processName, setProcessName] = useState(job?.process_name ?? '')
  const [contractPath, setContractPath] = useState(job?.contract_path ?? '')
  const [domain, setDomain] = useState(job?.domain ?? '')
  const [clientCode, setClientCode] = useState(job?.client_code ?? '')
  const [clientName, setClientName] = useState(job?.client_name ?? '')
  const [owner, setOwner] = useState(job?.owner ?? '')
  const [criticality, setCriticality] = useState(job?.criticality ?? '')
  const [sla, setSla] = useState(job?.sla ?? '')
  const [status, setStatus] = useState<JobStatus>(job?.status ?? 'active')
  const [reason, setReason] = useState('')

  const edicao = !!job

  return (
    <form
      className="panel"
      onSubmit={(e: FormEvent) => {
        e.preventDefault()
        const corpo: AdminJobCreate = {
          process_name: processName,
          contract_path: contractPath || null,
          domain: domain || null,
          client_code: clientCode || null,
          client_name: clientName || null,
          owner: owner || null,
          criticality: criticality || null,
          sla: sla || null,
          status,
          reason,
        }
        onSubmit(corpo)
      }}
    >
      <h2>{titulo}</h2>
      <div className="field">
        <label htmlFor="j-nome">Processo</label>
        <input
          id="j-nome"
          required
          disabled={edicao}
          value={processName}
          onChange={(e) => setProcessName(e.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor="j-contrato">Caminho do contrato (no host)</label>
        <input
          id="j-contrato"
          placeholder="/opt2/batch_v2/batch-commons-framework/processes/base2/nome.json"
          value={contractPath}
          onChange={(e) => setContractPath(e.target.value)}
        />
        <small className="muted">
          Precisa estar sob <code>.../processes/</code> — é o caminho que vai para{' '}
          <code>--process-file</code>.
        </small>
      </div>
      <div className="field">
        <label htmlFor="j-dominio">Domínio</label>
        <input id="j-dominio" value={domain} onChange={(e) => setDomain(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="j-cliente-codigo">Código do cliente</label>
        <input
          id="j-cliente-codigo"
          value={clientCode}
          onChange={(e) => setClientCode(e.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor="j-cliente-nome">Nome do cliente</label>
        <input
          id="j-cliente-nome"
          value={clientName}
          onChange={(e) => setClientName(e.target.value)}
        />
      </div>
      <div className="field">
        <label htmlFor="j-owner">Owner</label>
        <input id="j-owner" value={owner} onChange={(e) => setOwner(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="j-crit">Criticidade</label>
        <input id="j-crit" value={criticality} onChange={(e) => setCriticality(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="j-sla">SLA</label>
        <input id="j-sla" value={sla} onChange={(e) => setSla(e.target.value)} />
      </div>
      <div className="field">
        <label htmlFor="j-status">Status no catálogo</label>
        <select id="j-status" value={status} onChange={(e) => setStatus(e.target.value as JobStatus)}>
          {STATUS.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>
      <div className="field">
        <label htmlFor="j-motivo">Motivo (vai para a auditoria)</label>
        <input
          id="j-motivo"
          required
          minLength={3}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
      </div>
      <button type="submit" className="primary">
        Salvar
      </button>{' '}
      <button type="button" onClick={onCancel}>
        Cancelar
      </button>
    </form>
  )
}
