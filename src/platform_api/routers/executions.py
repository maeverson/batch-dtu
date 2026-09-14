"""`POST /executions` — execução manual e reprocesso, via `ExecutionBackend`.

Datas múltiplas: uma única invocação com `--dates-pattern-files` separado por
vírgula — é o `main.sh` quem serializa internamente (confirmado em
`tests/test_legacy_wrapper.py::test_multiplas_datas_sao_serializadas`). O
lock de `locking.py` impede uma SEGUNDA requisição concorrente para o MESMO
job — essa é a classe de incidente que o SOP Zinli documenta, não a
serialização de datas em si.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from catalog.db.models import AuditEvent, Execution, Job, JobContractVersion

from ..authz import Scope, require_operate, NotAuthorized
from ..deps import get_current_user, get_execution_backend, get_scope, get_session, load_job
from ..locking import JobLocked, try_lock_job
from ..schemas import ExecutionOut, ExecutionRequestIn
from ..security import AuthenticatedUser
from ..ssh_backend import ExecutionRequest, InvalidExecutionRequest, build_invocation

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


@router.post("", response_model=ExecutionOut, status_code=201)
async def executar(
    corpo: ExecutionRequestIn,
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

    try:
        try_lock_job(session, job.id)
    except JobLocked as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    request = ExecutionRequest(
        job=job, steps=corpo.steps, dates_pattern=datas, no_mail=corpo.no_mail,
    )
    # Validação de FORMATO antes de qualquer coisa — nada é persistido para
    # uma requisição que nunca seria aceita pelo wrapper.
    try:
        build_invocation(request, execution_id=str(uuid.uuid4()))
    except InvalidExecutionRequest as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    # Pré-validação (`--validate-file`) SEMPRE antes de executar
    # (`modules/platform-api/SPEC.md`, requisito 2) — é o `main.sh` real quem
    # decide se o contrato roda, não só o nosso schema local. Nada é
    # persistido se ela falhar: a execução nunca chegou a acontecer.
    pre_validacao = ExecutionRequest(
        job=job, steps=corpo.steps, dates_pattern=datas, no_mail=corpo.no_mail,
        validate_only=True,
    )
    resultado_validacao = await backend.dispatch(pre_validacao, execution_id=str(uuid.uuid4()))
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

    session.add(AuditEvent(
        actor=user.subject, action="execution.dispatch", target_type="execution",
        target_id=str(execution.id), source="api", occurred_at=agora,
        payload={"job_id": str(job.id), "process_name": job.process_name,
                "steps": corpo.steps, "dates_pattern": corpo.dates_pattern,
                "justification": corpo.justification},
    ))

    resultado = await backend.dispatch(request, execution_id=str(execution.id))

    fim = datetime.now(timezone.utc)
    execution.status = "succeeded" if resultado.exit_code == 0 else "failed"
    execution.result = resultado.result
    execution.exit_code = resultado.exit_code
    execution.ended_at = fim
    execution.log_link = f"execution_id={execution.id}"   # proxy real: ver logs.py

    session.add(AuditEvent(
        actor=user.subject, action="execution.completed", target_type="execution",
        target_id=str(execution.id), source="api", occurred_at=fim,
        payload={"result": execution.result, "exit_code": execution.exit_code},
    ))

    session.flush()
    return execution


@router.get("", response_model=list[ExecutionOut])
def listar(
    job_id: uuid.UUID | None = None,
    session: Session = Depends(get_session),
    scope: Scope = Depends(get_scope),
) -> list[Execution]:
    from ..authz import binding_matches

    consulta = select(Execution)
    if job_id:
        consulta = consulta.where(Execution.job_id == job_id)
    execucoes = list(session.scalars(consulta.order_by(Execution.started_at.desc())))

    jobs_vistos: dict[uuid.UUID, Job | None] = {}
    def _visivel(execucao: Execution) -> bool:
        if execucao.job_id not in jobs_vistos:
            jobs_vistos[execucao.job_id] = session.get(Job, execucao.job_id)
        job = jobs_vistos[execucao.job_id]
        return job is not None and any(binding_matches(b, job) for b in scope.bindings)

    return [e for e in execucoes if _visivel(e)]


def _load_execution_in_scope(execution_id: uuid.UUID, session: Session, scope: Scope) -> Execution:
    from ..authz import binding_matches

    execucao = session.get(Execution, execution_id)
    if execucao is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "execução não encontrada")
    job = session.get(Job, execucao.job_id)
    if job is None or not any(binding_matches(b, job) for b in scope.bindings):
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
    """Proxy de consulta ao Loki por `execution_id` (`docs/api/platform-api.md`).

    A CORRELAÇÃO log ↔ auditoria já existe (`execution.id` É o
    `execution_id`, gravado no nome do arquivo pelo wrapper — ver
    `docker/legacy/batch-wrapper.sh`); o QUE ainda falta é o agente que
    embarca o conteúdo do arquivo para o Loki (observability, Etapa 1.3).
    Este endpoint já funciona contra qualquer log que chegue lá rotulado por
    `execution_id`, hoje ou depois.
    """
    import httpx

    execucao = _load_execution_in_scope(execution_id, session, scope)
    base_url = request.app.state.loki_base_url
    inicio = execucao.started_at or execucao.created_at
    fim = execucao.ended_at or datetime.now(timezone.utc)

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
    linhas.sort(key=lambda l: l["timestamp_ns"])
    return {"execution_id": str(execution_id), "lines": linhas}
