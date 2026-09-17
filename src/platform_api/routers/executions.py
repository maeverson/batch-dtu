"""`POST /executions` — execução manual e reprocesso, via `ExecutionBackend`.

**A chamada é assíncrona do ponto de vista do cliente** (decisão do cliente,
G5): a API pré-valida, registra a execução, despacha o SSH e responde **200
com `status: running`**. Quem acompanha o andamento é o New Relic, pelo
`execution_id` — evento `BatchExecution` e o log que o wrapper grava com o
mesmo id no nome do arquivo. O Back Office reconsulta `GET /executions/{id}`
para exibir o desfecho.

O que isso troca, explicitamente: antes o operador recebia o exit code na
resposta; agora recebe a confirmação de que despachou. A pré-validação
(`--validate-file`) continua **síncrona**, porque é ela que impede um
despacho inválido de existir — sem ela o 200 seria uma promessa vazia.

Datas múltiplas: uma única invocação com `--dates-pattern-files` separado por
vírgula — é o `main.sh` quem serializa internamente (confirmado em
`tests/test_legacy_wrapper.py::test_multiplas_datas_sao_serializadas`).
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from catalog.db.models import AuditEvent, Execution, Job, JobContractVersion

from .. import telemetry
from ..authz import NotAuthorized, Scope, require_operate
from ..deps import get_current_user, get_execution_backend, get_scope, get_session, load_job
from ..locking import JobLocked, try_lock_job
from ..schemas import ExecutionOut, ExecutionRequestIn
from ..security import AuthenticatedUser
from ..ssh_backend import (
    ExecutionBackendUnavailable,
    ExecutionRequest,
    InvalidExecutionRequest,
    build_invocation,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/executions", tags=["execucao"])

_DATA_NUMERICA = re.compile(r"^\d{8}$")
_DATA_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _job_tem_upload_remote(session: Session, job: Job) -> bool:
    if not job.current_contract_version_id:
        return False
    versao = session.get(JobContractVersion, job.current_contract_version_id)
    if versao is None:
        return False
    steps = (versao.contract or {}).get("steps") or []
    return any(
        isinstance(s, dict) and s.get("function") in ("upload_remote", "download_remote")
        for s in steps
    )


def _join_datas(datas: list[str]) -> str:
    for d in datas:
        if not (_DATA_NUMERICA.match(d) or _DATA_ISO.match(d)):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"data fora do formato aceito (YYYYMMDD ou YYYY-MM-DD): {d!r}",
            )
    return ",".join(datas)


async def _concluir_execucao(
    *, session_factory: sessionmaker, backend, request: ExecutionRequest,
    execution_id: uuid.UUID, actor: str, environment: str,
) -> None:
    """Roda DEPOIS da resposta (BackgroundTasks). Sessão própria: a da
    requisição já foi encerrada quando isto começa.

    Nada aqui levanta para fora: a exceção não teria mais para quem subir — o
    cliente já recebeu 200. Todo desfecho, inclusive o inesperado, vira estado
    terminal na tabela `execution` e evento no New Relic. Execução que fica
    presa em `running` é justamente o que o alerta de ausência procura.
    """
    resultado = None
    erro: str | None = None
    try:
        resultado = await backend.dispatch(request, execution_id=str(execution_id))
    except ExecutionBackendUnavailable as exc:
        erro = str(exc)
    except Exception as exc:                                    # noqa: BLE001
        erro = f"falha inesperada no despacho: {exc}"
        logger.exception("despacho da execução %s falhou", execution_id)

    fim = datetime.now(timezone.utc)
    session = session_factory()
    try:
        execution = session.get(Execution, execution_id)
        if execution is None:                                   # nunca deveria
            logger.error("execução %s sumiu durante o despacho", execution_id)
            return

        if erro is not None:
            execution.status = "failed"
            execution.result = "failure"
            execution.ended_at = fim
            session.add(AuditEvent(
                actor=actor, action="execution.backend_unavailable", target_type="execution",
                target_id=str(execution_id), source="api", occurred_at=fim,
                payload={"error": erro},
            ))
        else:
            execution.status = "succeeded" if resultado.exit_code == 0 else "failed"
            execution.result = resultado.result
            execution.exit_code = resultado.exit_code
            execution.ended_at = fim
            session.add(AuditEvent(
                actor=actor, action="execution.completed", target_type="execution",
                target_id=str(execution_id), source="api", occurred_at=fim,
                payload={"result": execution.result, "exit_code": execution.exit_code},
            ))

        telemetry.record_execution_event({
            "execution_id": str(execution_id),
            "job_id": str(execution.job_id),
            "process_name": request.job.process_name,
            "domain": request.job.domain,
            "environment": environment,
            "host": execution.host,
            "requested_by": actor,
            "status": execution.status,
            "exit_code": execution.exit_code,
            "duration_ms": int(
                (fim - (execution.started_at or fim)).total_seconds() * 1000
            ),
            "error": erro,
        })
        session.commit()
    except Exception:                                           # noqa: BLE001
        session.rollback()
        logger.exception("falha ao registrar desfecho da execução %s", execution_id)
    finally:
        session.close()


@router.post("", response_model=ExecutionOut, status_code=status.HTTP_200_OK)
async def executar(
    corpo: ExecutionRequestIn,
    request: Request,
    tarefas: BackgroundTasks,
    session: Session = Depends(get_session),
    scope: Scope = Depends(get_scope),
    user: AuthenticatedUser = Depends(get_current_user),
    backend=Depends(get_execution_backend),
) -> Execution:
    job = load_job(corpo.job_id, session)
    try:
        require_operate(scope, job)
    except NotAuthorized as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.detail) from exc

    # Idempotência: reenvio da MESMA chave devolve a execução já registrada,
    # nunca dispara de novo (invariante 7 — salvaguarda contra reenvio
    # acidental a cliente).
    if corpo.idempotency_key:
        existente = session.scalar(
            select(Execution).where(Execution.idempotency_key == corpo.idempotency_key)
        )
        if existente is not None:
            return existente

    if not corpo.confirm_target_dates:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "confirm_target_dates é obrigatório — confirme a(s) data(s)-alvo antes de executar",
        )

    upload_remote_prod = job.environment == "PROD" and _job_tem_upload_remote(session, job)
    if upload_remote_prod and not corpo.confirm_upload_remote:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "este job envia arquivo a cliente em PROD (upload_remote/download_remote) — "
            "confirm_upload_remote é obrigatório (confirmação reforçada, docs/seguranca.md)",
        )

    datas = _join_datas(corpo.dates_pattern)

    # Duas travas de concorrência, porque o despacho agora sobrevive à
    # transação: o lock advisory protege a JANELA DE REGISTRO (duas
    # requisições simultâneas), e a checagem de execução `running` protege o
    # INTERVALO INTEIRO do despacho, que o lock não alcança mais — ele morre
    # no commit, o `main.sh` continua rodando.
    try:
        try_lock_job(session, job.id)
    except JobLocked as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    em_andamento = session.scalar(
        select(Execution).where(
            Execution.job_id == job.id, Execution.status == "running"
        ).limit(1)
    )
    if em_andamento is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"job já tem execução em andamento ({em_andamento.id}) — "
            "aguarde o desfecho ou consulte o New Relic por esse execution_id",
        )

    requisicao = ExecutionRequest(
        job=job, steps=corpo.steps, dates_pattern=datas, no_mail=corpo.no_mail,
    )
    # Validação de FORMATO antes de qualquer coisa — nada é persistido para
    # uma requisição que nunca seria aceita pelo wrapper.
    try:
        build_invocation(requisicao, execution_id=str(uuid.uuid4()))
    except InvalidExecutionRequest as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    # Pré-validação (`--validate-file`) SEMPRE antes de executar
    # (`modules/platform-api/SPEC.md`, requisito 2) — é o `main.sh` real quem
    # decide se o contrato roda, não só o nosso schema local. Síncrona de
    # propósito: é o que dá sentido ao 200 que devolvemos logo abaixo.
    pre_validacao = ExecutionRequest(
        job=job, steps=corpo.steps, dates_pattern=datas, no_mail=corpo.no_mail,
        validate_only=True,
    )
    try:
        resultado_validacao = await backend.dispatch(pre_validacao, execution_id=str(uuid.uuid4()))
    except ExecutionBackendUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
    if resultado_validacao.exit_code != 0:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"pré-validação (--validate-file) falhou: "
            f"{resultado_validacao.stderr or resultado_validacao.stdout}".strip(),
        )

    agora = datetime.now(timezone.utc)
    execution = Execution(
        job_id=job.id, trigger="manual" if not corpo.steps else "reprocess",
        requested_steps=[corpo.steps] if corpo.steps else [],
        dates_pattern=corpo.dates_pattern, no_mail=corpo.no_mail,
        status="running", requested_by=user.subject, justification=corpo.justification,
        idempotency_key=corpo.idempotency_key, backend="ssh", host=job.host,
        started_at=agora,
    )
    session.add(execution)
    session.flush()   # gera execution.id — É o execution_id (ver models.Execution)
    execution.log_link = f"execution_id={execution.id}"

    session.add(AuditEvent(
        actor=user.subject, action="execution.dispatch", target_type="execution",
        target_id=str(execution.id), source="api", occurred_at=agora,
        payload={"job_id": str(job.id), "process_name": job.process_name,
                 "steps": corpo.steps, "dates_pattern": corpo.dates_pattern,
                 "justification": corpo.justification,
                 "actor_display_name": user.display_name},
    ))
    # Commit ANTES de agendar o despacho: a tarefa de fundo abre a própria
    # sessão e precisa enxergar esta execução. Sem isto ela procura uma linha
    # que ainda não foi confirmada e o desfecho nunca é gravado — a execução
    # ficaria presa em `running` para sempre.
    session.commit()

    settings = request.app.state.settings
    telemetry.add_execution_context(str(execution.id), job, settings.environment)
    tarefas.add_task(
        _concluir_execucao,
        session_factory=request.app.state.session_factory,
        backend=backend,
        request=requisicao,
        execution_id=execution.id,
        actor=user.subject,
        environment=settings.environment,
    )
    return execution


@router.get("", response_model=list[ExecutionOut])
def listar(
    job_id: uuid.UUID | None = None,
    session: Session = Depends(get_session),
    scope: Scope = Depends(get_scope),
) -> list[Execution]:
    consulta = select(Execution)
    if job_id:
        consulta = consulta.where(Execution.job_id == job_id)
    execucoes = list(session.scalars(consulta.order_by(Execution.started_at.desc())))

    jobs_vistos: dict[uuid.UUID, Job | None] = {}

    def _visivel(execucao: Execution) -> bool:
        if execucao.job_id not in jobs_vistos:
            jobs_vistos[execucao.job_id] = session.get(Job, execucao.job_id)
        job = jobs_vistos[execucao.job_id]
        return job is not None and scope.can_view(job)

    return [e for e in execucoes if _visivel(e)]


def _load_execution_in_scope(execution_id: uuid.UUID, session: Session, scope: Scope) -> Execution:
    execucao = session.get(Execution, execution_id)
    if execucao is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "execução não encontrada")
    job = session.get(Job, execucao.job_id)
    if job is None or not scope.can_view(job):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "sem escopo sobre o job desta execução")
    return execucao


@router.get("/{execution_id}", response_model=ExecutionOut)
def obter(execution_id: uuid.UUID, session: Session = Depends(get_session),
          scope: Scope = Depends(get_scope)) -> Execution:
    return _load_execution_in_scope(execution_id, session, scope)


@router.get("/{execution_id}/logs")
async def logs(
    execution_id: uuid.UUID,
    request: Request,
    session: Session = Depends(get_session),
    scope: Scope = Depends(get_scope),
) -> dict:
    """Log correlacionado por `execution_id` — New Relic em ambiente
    implantado, Loki no `docker-compose.yaml` local (`LOG_BACKEND`).

    O contrato de resposta é o mesmo nos dois: a troca de backend de
    observabilidade não muda a API que o Back Office consome.
    """
    execucao = _load_execution_in_scope(execution_id, session, scope)
    settings = request.app.state.settings

    inicio = execucao.started_at or execucao.created_at
    fim = execucao.ended_at or datetime.now(timezone.utc)
    # Margem: o agente de log tem atraso de segundos, e o relógio do host
    # executor não é o mesmo deste processo. Uma janela colada em
    # [started_at, ended_at] devolve execução sem log nenhum — indistinguível,
    # para quem opera, de execução que não logou.
    inicio = inicio - timedelta(minutes=1)
    fim = fim + timedelta(minutes=5)

    if settings.log_backend == "newrelic":
        from ..newrelic_logs import NewRelicLogsUnavailable, fetch_logs

        try:
            linhas = await fetch_logs(
                account_id=settings.newrelic.account_id,
                api_key=settings.newrelic.api_key,
                nerdgraph_url=settings.newrelic.nerdgraph_url,
                execution_id=str(execution_id), inicio=inicio, fim=fim,
            )
        except NewRelicLogsUnavailable as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
        return {"execution_id": str(execution_id), "backend": "newrelic", "lines": linhas}

    import httpx

    base_url = request.app.state.loki_base_url
    async with httpx.AsyncClient(timeout=10.0) as client:
        resposta = await client.get(
            f"{base_url}/loki/api/v1/query_range",
            params={
                "query": f'{{execution_id="{execution_id}"}}',
                "start": str(int(inicio.timestamp() * 1e9)),
                "end": str(int(fim.timestamp() * 1e9) + 1_000_000_000),
                "limit": 1000,
            },
        )
    if resposta.status_code != 200:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Loki respondeu {resposta.status_code}")

    corpo = resposta.json()
    linhas = [
        {"timestamp_ns": ts, "line": linha}
        for stream in (corpo.get("data") or {}).get("result", [])
        for ts, linha in stream.get("values", [])
    ]
    linhas.sort(key=lambda linha: linha["timestamp_ns"])
    return {"execution_id": str(execution_id), "backend": "loki", "lines": linhas}
