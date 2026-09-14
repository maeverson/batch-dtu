import { useState } from 'react'
import { apiPost, ApiError } from '../api/client'
import { paths } from '../api/endpoints'
import { useToken } from '../api/hooks'
import type { Execution, ExecutionRequest, Job, JobContract } from '../api/types'

const STEPS_PATTERN = /^\d+([,-]\d+)*$/

type Phase = 'form' | 'confirm-dates' | 'confirm-upload-remote' | 'submitting' | 'error'

function jobHasUploadRemote(contract: JobContract | undefined): boolean {
  return !!contract?.contract.steps?.some(
    (s) => s.function === 'upload_remote' || s.function === 'download_remote',
  )
}

export function ExecuteModal({
  job,
  contract,
  onClose,
  onExecuted,
}: {
  job: Job
  contract: JobContract | undefined
  onClose: () => void
  onExecuted: (execution: Execution) => void
}) {
  const token = useToken()
  const [steps, setSteps] = useState('')
  const [dateInput, setDateInput] = useState('')
  const [dates, setDates] = useState<string[]>([])
  const [noMail, setNoMail] = useState(false)
  const [justification, setJustification] = useState('')
  const [retype, setRetype] = useState('')
  const [phase, setPhase] = useState<Phase>('form')
  const [errorMsg, setErrorMsg] = useState<string>()

  const exigeConfirmacaoReforcada = job.environment === 'PROD' && jobHasUploadRemote(contract)
  const stepsValido = steps === '' || STEPS_PATTERN.test(steps)
  const formValido = dates.length > 0 && justification.trim().length >= 3 && stepsValido

  function adicionarData() {
    if (!dateInput) return
    const normalizada = dateInput.replaceAll('-', '')
    if (!dates.includes(normalizada)) setDates([...dates, normalizada])
    setDateInput('')
  }

  function removerData(d: string) {
    setDates(dates.filter((x) => x !== d))
  }

  async function executar(confirmUploadRemote: boolean) {
    setPhase('submitting')
    setErrorMsg(undefined)
    const payload: ExecutionRequest = {
      job_id: job.id,
      steps: steps || undefined,
      dates_pattern: dates,
      confirm_target_dates: true,
      confirm_upload_remote: confirmUploadRemote,
      no_mail: noMail,
      justification,
    }
    try {
      const execucao = await apiPost<Execution>(paths.executions({}), token, payload)
      onExecuted(execucao)
    } catch (e) {
      setErrorMsg(e instanceof ApiError ? e.message : String(e))
      setPhase('error')
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal__header">
          <h2>Executar {job.process_name}</h2>
          <button onClick={onClose} aria-label="Fechar">✕</button>
        </div>

        {phase === 'form' && (
          <div>
            <div className="field">
              <label htmlFor="steps">Steps (--manual-steps, opcional)</label>
              <input id="steps" type="text" placeholder="ex.: 3 ou 3,4-6" value={steps}
                     onChange={(e) => setSteps(e.target.value)} />
              <span className="hint">Vazio = todos os steps do contrato.</span>
              {!stepsValido && <span className="hint" style={{ color: 'var(--error)' }}>
                Formato inválido — use "3" ou "3,4-6".
              </span>}
            </div>

            <div className="field">
              <label htmlFor="date-input">Data(s)-alvo</label>
              <div style={{ display: 'flex', gap: 8 }}>
                <input id="date-input" type="date" value={dateInput}
                       onChange={(e) => setDateInput(e.target.value)} />
                <button type="button" onClick={adicionarData}>Adicionar data</button>
              </div>
              {dates.length > 0 && (
                <ul style={{ margin: '8px 0 0', paddingLeft: 18 }}>
                  {dates.map((d) => (
                    <li key={d} className="mono">
                      {d}{' '}
                      <button type="button" className="link" onClick={() => removerData(d)}>remover</button>
                    </li>
                  ))}
                </ul>
              )}
              {dates.length > 1 && (
                <span className="hint">
                  Múltiplas datas viram UMA execução serializada nesta ordem — o processo não paraleliza.
                </span>
              )}
            </div>

            <div className="field">
              <label>
                <input type="checkbox" checked={noMail} onChange={(e) => setNoMail(e.target.checked)} />
                {' '}--no-mail
              </label>
            </div>

            <div className="field">
              <label htmlFor="justification">Justificativa</label>
              <textarea id="justification" rows={2} value={justification}
                        onChange={(e) => setJustification(e.target.value)}
                        placeholder="por que esta execução manual é necessária" />
            </div>

            <div className="modal__header" style={{ marginTop: 16 }}>
              <button onClick={onClose}>Cancelar</button>
              <button className="primary" disabled={!formValido}
                      onClick={() => setPhase('confirm-dates')}>
                Continuar
              </button>
            </div>
          </div>
        )}

        {phase === 'confirm-dates' && (
          <div>
            <div className="confirm-banner">
              Confirme a(s) data(s)-alvo antes de disparar — não há como cancelar depois de enviado.
            </div>
            <p>
              <strong>{job.process_name}</strong> ({job.environment}), steps <code>{steps || 'todos'}</code>,
              data(s):
            </p>
            <ul>
              {dates.map((d) => <li key={d} className="mono">{d}</li>)}
            </ul>
            {dates.length > 1 && (
              <p className="muted">
                Serialização: {dates.map((d, i) => `${i + 1}ª ${d}`).join(' → ')}.
                Uma segunda execução deste processo enquanto esta roda recebe 409 (sem fila).
              </p>
            )}
            <div className="modal__header" style={{ marginTop: 16 }}>
              <button onClick={() => setPhase('form')}>Voltar</button>
              <button className="primary"
                      onClick={() => (exigeConfirmacaoReforcada ? setPhase('confirm-upload-remote') : executar(false))}>
                Confirmar data-alvo
              </button>
            </div>
          </div>
        )}

        {phase === 'confirm-upload-remote' && (
          <div>
            <div className="confirm-banner">
              Este job envia arquivo a cliente em PROD (<code>upload_remote</code>/<code>download_remote</code>).
              Confirmação reforçada: redigite exatamente a(s) data(s)-alvo abaixo para liberar o envio.
            </div>
            <p className="mono">Esperado: {dates.join(',')}</p>
            <div className="field">
              <label htmlFor="retype">Redigite a(s) data(s) (separadas por vírgula, AAAAMMDD)</label>
              <input id="retype" type="text" value={retype} onChange={(e) => setRetype(e.target.value)} />
            </div>
            <div className="modal__header" style={{ marginTop: 16 }}>
              <button onClick={() => setPhase('confirm-dates')}>Voltar</button>
              <button className="danger" disabled={retype.trim() !== dates.join(',')}
                      onClick={() => executar(true)}>
                Confirmar envio a cliente e executar
              </button>
            </div>
          </div>
        )}

        {phase === 'submitting' && (
          <p className="muted">Executando — a chamada fica presa até o job terminar (backend síncrono na Fase 1). Não feche esta janela.</p>
        )}

        {phase === 'error' && (
          <div>
            <div className="error-banner">{errorMsg}</div>
            <div className="modal__header">
              <button onClick={onClose}>Fechar</button>
              <button className="primary" onClick={() => setPhase('confirm-dates')}>Tentar de novo</button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
