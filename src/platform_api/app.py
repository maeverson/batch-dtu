"""Factory da aplicação — `create_app()` monta tudo a partir de `Settings` e
de um `ExecutionBackend`, nunca de globals de módulo. É isso que permite o
teste construir o app inteiro contra Postgres real com um backend em memória,
sem subir SSH nenhum, e o `cli.py` construir o mesmo app contra o backend SSH
de verdade — mesmo código, dependências trocadas."""

from __future__ import annotations

from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from catalog.db.session import database_url

from .config import Settings
from .routers import audit, change_requests, executions, jobs
from .security import TokenVerifier
from .ssh_backend import SSHExecutionBackend


def create_app(settings: Settings | None = None, *, execution_backend=None,
               engine=None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(
        title="Batch DTU — Platform API",
        description="API REST que abstrai o mecanismo de execução do parque "
                    "de jobs agendados (ver docs/api/platform-api.md).",
        version="0.1.0",
    )

    bound = engine or create_engine(database_url(settings.db_role), future=True, pool_pre_ping=True)
    app.state.session_factory = sessionmaker(bind=bound, expire_on_commit=False, future=True)
    app.state.token_verifier = TokenVerifier(settings.oidc)
    app.state.execution_backend = execution_backend or SSHExecutionBackend(
        host=settings.ssh.host, port=settings.ssh.port, username=settings.ssh.username,
        key_path=settings.ssh.key_path, connect_timeout=settings.ssh.connect_timeout,
        known_hosts=settings.ssh.known_hosts,
    )
    app.state.loki_base_url = settings.loki.base_url
    app.state.settings = settings

    app.include_router(jobs.router)
    app.include_router(executions.router)
    app.include_router(change_requests.router)
    app.include_router(audit.router)

    @app.get("/health", tags=["operacional"])
    def health() -> dict:
        return {"status": "ok"}

    return app
