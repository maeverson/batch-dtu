"""`GET/PATCH /jobs` — catálogo de leitura + habilitar/desabilitar (Fase 1).

`PATCH /jobs/{id}/status` **não escreve no crontab** (invariante 2). Ele grava
o estado desejado no `job` e abre um `crontab_change_request`: a aplicação é
manual, a verificação é automática (`catalog reconcile`, que já fecha o loop
— ver `src/catalog/db/reconcile.py`). Ver `modules/platform-api/CLAUDE.md`
para o ciclo completo.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from catalog.contract_schema import validate_contract
from catalog.db.models import (
    AuditEvent,
    CrontabChangeRequest,
    Job,
    JobContractVersion,
    JobRevision,
    JobSchedule,
    ReconciliationFinding,
    ReconciliationRun,
)

from ..authz import Scope
from ..deps import get_current_user, get_scope, get_session, job_for_operate, job_for_view
from ..schemas import (
    ChangeRequestOut,
    ErrorOut,
    JobContractOut,
    JobOut,
    JobScheduleOut,
    JobStatusChange,
    ReconciliationFindingOut,
    ReconciliationStateOut,
)
from ..security import AuthenticatedUser

router = APIRouter(prefix="/jobs", tags=["catalogo"])

# Tempo até uma change_request pendente virar `expired` — ver
# `modules/platform-api/CLAUDE.md` ("pendência além do SLA"). 48h é o
# exemplo do próprio CLAUDE.md; revisar se a operação real pedir outro valor.
_CHANGE_REQUEST_SLA = timedelta(hours=48)


@router.get("", response_model=list[JobOut])
def listar(
    domain: str | None = None,
    client: str | None = Query(None, alias="client"),
    environment: str | None = None,
    status_: str | None = Query(None, alias="status"),
    session: Session = Depends(get_session),
    scope: Scope = Depends(get_scope),
) -> list[Job]:
    # Uma instância serve UM ambiente e UM host (G1 / `config.Settings`): o
    # filtro nasce no SQL, não na role. Um job de PROD não aparece na
    # instância de UAT nem para `batch.admin` — é a separação do deploy, e é
    # ela que garante que o canal SSH daqui só alcança o host declarado.
    consulta = select(Job).where(Job.environment == scope.environment, Job.host == scope.host)
    if domain:
        consulta = consulta.where(Job.domain == domain)
    if client:
        consulta = consulta.where((Job.client_code == client) | (Job.client_name == client))
    if environment:
        consulta = consulta.where(Job.environment == environment)
    if status_:
        consulta = consulta.where(Job.status == status_)

    todos = list(session.scalars(consulta.order_by(Job.process_name)))
    return [j for j in todos if scope.can_view(j)]


@router.get("/{job_id}", response_model=JobOut)
def obter(job: Job = Depends(job_for_view)) -> Job:
    return job


@router.get("/{job_id}/schedules", response_model=list[JobScheduleOut])
def agendas(
    job: Job = Depends(job_for_view), session: Session = Depends(get_session)
) -> list[JobSchedule]:
    return list(
        session.scalars(
            select(JobSchedule).where(JobSchedule.job_id == job.id)
            .order_by(JobSchedule.cron_lineno)
        )
    )


@router.get(
    "/{job_id}/contract",
    response_model=JobContractOut,
    responses={404: {"model": ErrorOut}},
)
def contrato_corrente(
    job: Job = Depends(job_for_view), session: Session = Depends(get_session)
) -> JobContractVersion:
    """Contrato JSON da versão corrente — o mesmo que `catalog history
    <processo>` mostra (`ROADMAP.md`, Etapa 1.1), aqui via REST."""
    if job.current_contract_version_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job sem versão de contrato")
    versao = session.get(JobContractVersion, job.current_contract_version_id)
    if versao is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "versão de contrato ausente")
    return versao


@router.get("/{job_id}/reconciliation", response_model=ReconciliationStateOut)
def estado_de_reconciliacao(
    job: Job = Depends(job_for_view), session: Session = Depends(get_session)
) -> ReconciliationStateOut:
    """Divergências ABERTAS do job na reconciliação mais recente do HOST dele
    (`catalog reconcile` roda por host — ver `CLAUDE.md` raiz). Alimenta o
    item "estado da reconciliação" do enable/disable no Back Office."""
    ultima_run = session.scalar(
        select(ReconciliationRun).where(ReconciliationRun.host == job.host)
        .order_by(ReconciliationRun.started_at.desc()).limit(1)
    )
    if ultima_run is None:
        return ReconciliationStateOut(
            host=job.host, state="nunca_rodou", run_id=None,
            run_finished_at=None, open_findings=[],
        )

    achados = list(
        session.scalars(
            select(ReconciliationFinding).where(
                ReconciliationFinding.run_id == ultima_run.id,
                ReconciliationFinding.job_id == job.id,
                ReconciliationFinding.status == "open",
            )
        )
    )
    return ReconciliationStateOut(
        host=job.host, state="divergente" if achados else "ok",
        run_id=ultima_run.id, run_finished_at=ultima_run.finished_at,
        open_findings=[ReconciliationFindingOut.model_validate(a) for a in achados],
    )


@router.post(
    "/{job_id}/validate",
    responses={422: {"model": ErrorOut}},
)
def validar(job: Job = Depends(job_for_view), session: Session = Depends(get_session)) -> dict:
    """Equivalente a `--validate-file`: valida o contrato CORRENTE contra o
    schema (`catalog/contract_schema.py`) sem despachar nada por SSH."""
    if job.current_contract_version_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "job sem versão de contrato")
    versao = session.get(JobContractVersion, job.current_contract_version_id)
    if versao is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "versão de contrato ausente")
    relatorio = validate_contract(versao.contract)
    return relatorio.as_dict()


def _linha_alvo(job: Job, schedules: list[JobSchedule], desired_status: str, marcador: str) -> str:
    """A linha-alvo a aplicar — não um diff do arquivo. Um diff calculado
    agora pode não aplicar mais quando o operador for aplicar de fato
    (`modules/platform-api/CLAUDE.md`)."""
    linhas = [marcador]
    fonte = schedules or [None]
    for agenda in fonte:
        base = (agenda.raw_line if agenda and agenda.raw_line else None)
        if base is None:
            base = f"{(agenda.schedule_expr if agenda else '@reboot')} {job.wrapper_path or job.contract_path}"
        nucleo = base.strip().lstrip("#").strip()
        linhas.append(f"#{nucleo}" if desired_status == "disabled" else nucleo)
    return "\n".join(linhas)


@router.patch("/{job_id}/status", response_model=ChangeRequestOut, status_code=201)
def mudar_status(
    corpo: JobStatusChange,
    job: Job = Depends(job_for_operate),
    session: Session = Depends(get_session),
    user: AuthenticatedUser = Depends(get_current_user),
) -> CrontabChangeRequest:
    if job.status == corpo.desired_status:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"job já está com status '{corpo.desired_status}'",
        )

    antes = {"status": job.status, "status_reason": job.status_reason}
    agora = datetime.now(timezone.utc)
    change_id = uuid.uuid4()
    marcador = f"#BO:{job.id}:{change_id} {corpo.reason}"

    schedules = list(
        session.scalars(select(JobSchedule).where(JobSchedule.job_id == job.id))
    )
    instrucao = _linha_alvo(job, schedules, corpo.desired_status, marcador)

    change = CrontabChangeRequest(
        id=change_id, job_id=job.id, host=job.host, desired_status=corpo.desired_status,
        reason=corpo.reason, instruction=instrucao, marker=marcador,
        state="pending", requested_by=user.subject, requested_at=agora,
        expires_at=agora + _CHANGE_REQUEST_SLA,
    )
    session.add(change)

    job.status = corpo.desired_status
    job.status_reason = corpo.reason
    job.updated_by = user.subject

    ultima_rev = session.scalar(
        select(JobRevision.version).where(JobRevision.job_id == job.id)
        .order_by(JobRevision.version.desc()).limit(1)
    )
    session.add(JobRevision(
        job_id=job.id, version=(ultima_rev or 0) + 1,
        snapshot={"status": job.status, "status_reason": job.status_reason},
        diff=antes, created_by=user.subject,
    ))

    session.add(AuditEvent(
        actor=user.subject, action="job.status_change", target_type="job",
        target_id=str(job.id), source="api", occurred_at=agora,
        payload={"desired_status": corpo.desired_status, "reason": corpo.reason,
                "change_request_id": str(change_id), "before": antes},
    ))

    session.flush()
    return change
