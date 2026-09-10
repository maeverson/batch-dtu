"""Camada de persistência do catálogo."""

from .models import (  # noqa: F401
    APPEND_ONLY_TABLES,
    AuditEvent,
    Base,
    ConnectionAlias,
    CrontabEntryRow,
    CrontabSnapshot,
    Job,
    JobConnectionAlias,
    JobContractVersion,
    JobRevision,
    JobSchedule,
    ReconciliationFinding,
    ReconciliationRun,
    SCHEMA,
)
from .session import database_url, session_scope  # noqa: F401
