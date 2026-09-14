"""`GET /change-requests` (worklist do Back Office) e cancelamento.

Aplicar a linha-alvo continua manual na Fase 1; quem fecha o ciclo
(`applied → verified`) é `catalog reconcile` (`src/catalog/db/reconcile.py`),
por detecção — nenhum endpoint aqui marca "verified" a pedido do operador.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from catalog.db.models import AuditEvent, CrontabChangeRequest, Job

from ..authz import Scope, binding_matches
from ..deps import get_current_user, get_scope, get_session
from ..schemas import ChangeRequestOut
from ..security import AuthenticatedUser

router = APIRouter(prefix="/change-requests", tags=["mudanca-de-agendamento"])


class CancelIn(BaseModel):
    reason: str = Field(..., min_length=3)


@router.get("", response_model=list[ChangeRequestOut])
def listar(
    state: str | None = None,
    host: str | None = None,
    session: Session = Depends(get_session),
    scope: Scope = Depends(get_scope),
) -> list[CrontabChangeRequest]:
    consulta = select(CrontabChangeRequest)
    if state:
        consulta = consulta.where(CrontabChangeRequest.state == state)
    if host:
        consulta = consulta.where(CrontabChangeRequest.host == host)

    pedidos = list(session.scalars(consulta.order_by(CrontabChangeRequest.requested_at.desc())))
    jobs: dict[uuid.UUID, Job | None] = {}

    def _visivel(pedido: CrontabChangeRequest) -> bool:
        if pedido.job_id not in jobs:
            jobs[pedido.job_id] = session.get(Job, pedido.job_id)
        job = jobs[pedido.job_id]
        return job is not None and any(binding_matches(b, job) for b in scope.bindings)

    return [p for p in pedidos if _visivel(p)]


@router.post("/{change_id}/cancel", response_model=ChangeRequestOut)
def cancelar(
    change_id: uuid.UUID,
    corpo: CancelIn,
    session: Session = Depends(get_session),
    scope: Scope = Depends(get_scope),
    user: AuthenticatedUser = Depends(get_current_user),
) -> CrontabChangeRequest:
    from ..authz import NotAuthorized, require_operate

    pedido = session.get(CrontabChangeRequest, change_id)
    if pedido is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "change_request não encontrada")
    if not pedido.is_open:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"change_request já está '{pedido.state}'"
        )

    job = session.get(Job, pedido.job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "job da change_request não encontrado")
    try:
        require_operate(scope, job)
    except NotAuthorized as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.detail) from exc

    agora = datetime.now(timezone.utc)
    pedido.state = "cancelled"

    session.add(AuditEvent(
        actor=user.subject, action="change_request.cancel", target_type="crontab_change_request",
        target_id=str(pedido.id), source="api", occurred_at=agora,
        payload={"reason": corpo.reason, "job_id": str(job.id)},
    ))
    session.flush()
    return pedido
