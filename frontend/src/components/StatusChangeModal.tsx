import { useState } from 'react'
import { apiPatch, ApiError } from '../api/client'
import { paths } from '../api/endpoints'
import { useToken } from '../api/hooks'
import type { ChangeRequest, Job } from '../api/types'

export function StatusChangeModal({
  job,
  onClose,
  onChanged,
}: {
  job: Job
  onClose: () => void
  onChanged: (cr: ChangeRequest) => void
}) {
  const token = useToken()
  const desiredStatus = job.status === 'active' ? 'disabled' : 'active'
  const [reason, setReason] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [errorMsg, setErrorMsg] = useState<string>()

  async function confirmar() {
    setSubmitting(true)
    setErrorMsg(undefined)
    try {
      const cr = await apiPatch<ChangeRequest>(paths.jobStatus(job.id), token, {
        desired_status: desiredStatus,
        reason,
      })
      onChanged(cr)
    } catch (e) {
      setErrorMsg(e instanceof ApiError ? e.message : String(e))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal__header">
          <h2>{desiredStatus === 'disabled' ? 'Desabilitar' : 'Reabilitar'} {job.process_name}</h2>
          <button onClick={onClose} aria-label="Fechar">✕</button>
        </div>

        <p className="muted">
          Isto grava o estado desejado no catálogo e abre uma <code>crontab_change_request</code> —
          não edita o crontab. A linha-alvo a aplicar aparece depois de confirmar; a verificação é
          automática, pela próxima reconciliação.
        </p>

        <div className="field">
          <label htmlFor="reason">Motivo (obrigatório)</label>
          <textarea id="reason" rows={2} value={reason} onChange={(e) => setReason(e.target.value)}
                    placeholder="ex.: cliente suspendeu o contrato" />
        </div>

        {errorMsg && <div className="error-banner">{errorMsg}</div>}

        <div className="modal__header" style={{ marginTop: 16 }}>
          <button onClick={onClose}>Cancelar</button>
          <button className={desiredStatus === 'disabled' ? 'danger' : 'primary'}
                  disabled={reason.trim().length < 3 || submitting}
                  onClick={confirmar}>
            {submitting ? 'Enviando…' : `Confirmar ${desiredStatus === 'disabled' ? 'desabilitação' : 'reabilitação'}`}
          </button>
        </div>
      </div>
    </div>
  )
}
