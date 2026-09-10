"""Engine e sessão. A URL vem do ambiente — nunca hardcoded."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_URL = "postgresql+psycopg://batch_app:batch_app_dev@localhost:5432/batch_catalog"


def database_url(role: str = "app") -> str:
    """URL por papel: `app` (aplicação), `migrations` (dona do schema), `test`."""
    variables = {
        "app": "DATABASE_URL",
        "migrations": "DATABASE_URL_MIGRATIONS",
        "test": "DATABASE_URL_TEST",
    }
    name = variables.get(role, "DATABASE_URL")
    return os.environ.get(name) or os.environ.get("DATABASE_URL") or DEFAULT_URL


def make_engine(role: str = "app", **kwargs) -> Engine:
    return create_engine(database_url(role), future=True, pool_pre_ping=True, **kwargs)


@contextmanager
def session_scope(role: str = "app", engine: Engine | None = None) -> Iterator[Session]:
    """Transação única por unidade de trabalho.

    O `audit_event` da mudança precisa ser gravado na MESMA transação que a
    mudança — é o que fecha a janela em que uma escrita existiria sem trilha.
    """
    bound = engine or make_engine(role)
    factory = sessionmaker(bind=bound, expire_on_commit=False, future=True)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        if engine is None:
            bound.dispose()
