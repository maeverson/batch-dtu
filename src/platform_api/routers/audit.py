"""`GET /audit-events` — leitura da trilha, append-only.

Requer `batch.admin`: auditoria atravessa job/domínio/ambiente (um evento de
`catalog.import` não pertence a job nenhum), então o escopo por-job de
`role_binding` não dá conta de filtrar isto com segurança — MVP decide por
role global, revisitar se um caso de uso pedir escopo mais fino.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from catalog.db.models import AuditEvent

from ..deps import get_current_user, get_session
from ..schemas import AuditEventOut
from ..security import AuthenticatedUser

router = APIRouter(prefix="/audit-events", tags=["auditoria"])


@router.get("", response_model=list[AuditEventOut])
def listar(
    actor: str | None = None,
    action: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    from_: datetime | None = None,
    to: datetime | None = None,
    limit: int = 200,
    session: Session = Depends(get_session),
    user: AuthenticatedUser = Depends(get_current_user),
) -> list[AuditEvent]:
    if not user.has_role("batch.admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "leitura de auditoria exige 'batch.admin'")

    consulta = select(AuditEvent)
    if actor:
        consulta = consulta.where(AuditEvent.actor == actor)
    if action:
        consulta = consulta.where(AuditEvent.action == action)
    if target_type:
        consulta = consulta.where(AuditEvent.target_type == target_type)
    if target_id:
        consulta = consulta.where(AuditEvent.target_id == target_id)
    if from_:
        consulta = consulta.where(AuditEvent.occurred_at >= from_)
    if to:
        consulta = consulta.where(AuditEvent.occurred_at <= to)

    consulta = consulta.order_by(AuditEvent.occurred_at.desc()).limit(min(limit, 1000))
    return list(session.scalars(consulta))
