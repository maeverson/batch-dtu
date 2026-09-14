"""Dependências FastAPI — sessão de banco, usuário autenticado, backend de
execução. Tudo lido de `request.app.state`, nunca de globals de módulo: é o
que permite o teste trocar backend/settings sem tocar processo nenhum
(`app.dependency_overrides`, ou construir o app com outro `state`)."""

from __future__ import annotations

from collections.abc import Generator

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from catalog.db.models import Job

from .authz import NotAuthorized, Scope, require_operate, require_view, resolve_scope
from .security import AuthenticatedUser, TokenInvalid, TokenVerifier

_bearer = HTTPBearer(auto_error=False)


def get_session(request: Request) -> Generator[Session, None, None]:
    factory = request.app.state.session_factory
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> AuthenticatedUser:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token ausente",
                            headers={"WWW-Authenticate": "Bearer"})
    verifier: TokenVerifier = request.app.state.token_verifier
    try:
        return verifier.verify(credentials.credentials)
    except TokenInvalid as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc),
                            headers={"WWW-Authenticate": "Bearer"}) from exc


def get_scope(
    user: AuthenticatedUser = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Scope:
    return resolve_scope(session, user)


def get_execution_backend(request: Request):
    return request.app.state.execution_backend


def load_job(job_id, session: Session) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"job {job_id} não encontrado")
    return job


def job_for_view(job_id, session: Session = Depends(get_session),
                 scope: Scope = Depends(get_scope)) -> Job:
    job = load_job(job_id, session)
    try:
        require_view(scope, job)
    except NotAuthorized as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.detail) from exc
    return job


def job_for_operate(job_id, session: Session = Depends(get_session),
                    scope: Scope = Depends(get_scope)) -> Job:
    job = load_job(job_id, session)
    try:
        require_operate(scope, job)
    except NotAuthorized as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, exc.detail) from exc
    return job
