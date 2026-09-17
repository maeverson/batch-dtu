"""Formas de requisição/resposta — espelham `docs/api/platform-api.md`.

O contrato REST não muda entre fases (`modules/platform-api/CLAUDE.md`); estes
schemas são o que garante isso em código, não só em documentação.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class JobOut(BaseModel):
    id: UUID
    host: str
    process_name: str
    domain: str | None
    environment: str | None
    client_code: str | None
    client_name: str | None
    country_codes: list[str]
    status: str
    status_reason: str | None
    kind: str
    contract_path: str | None
    owner: str | None
    criticality: str | None
    sla: str | None
    current_contract_version_id: UUID | None
    updated_at: datetime

    model_config = {"from_attributes": True}


class JobScheduleOut(BaseModel):
    id: UUID
    schedule_expr: str | None
    timezone: str
    enabled: bool
    catchup_policy: str | None
    manual_steps: str | None
    dates_pattern: str | None
    no_mail: bool
    raw_line: str | None

    model_config = {"from_attributes": True}


class JobContractOut(BaseModel):
    version: int
    schema_version: str | None
    contract: dict
    contract_hash: str
    validation_status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ReconciliationFindingOut(BaseModel):
    id: UUID
    kind: str
    severity: str
    subject: str
    detail: str | None
    status: str
    explanation: str | None
    fingerprint: str

    model_config = {"from_attributes": True}


class ReconciliationStateOut(BaseModel):
    host: str
    state: str = Field(..., description="'ok' | 'divergente' | 'nunca_rodou'")
    run_id: UUID | None
    run_finished_at: datetime | None
    open_findings: list[ReconciliationFindingOut]


class MeOut(BaseModel):
    subject: str
    display_name: str | None = None
    roles: list[str] = Field(..., description="App roles do Entra ID presentes no token")
    environment: str = Field(..., description="Ambiente que ESTA instância serve")
    host: str = Field(..., description="Host do catálogo que ESTA instância opera")
    is_admin: bool = False


class JobStatusChange(BaseModel):
    desired_status: str
    reason: str = Field(..., min_length=3)

    @field_validator("desired_status")
    @classmethod
    def _status_valido(cls, v: str) -> str:
        if v not in ("active", "disabled"):
            raise ValueError("desired_status deve ser 'active' ou 'disabled'")
        return v


class ChangeRequestOut(BaseModel):
    id: UUID
    job_id: UUID
    host: str
    desired_status: str
    reason: str
    state: str
    marker: str
    instruction: str | None
    requested_by: str
    requested_at: datetime
    expires_at: datetime | None
    applied_by: str | None
    applied_at: datetime | None
    verified_at: datetime | None

    model_config = {"from_attributes": True}


class ExecutionRequestIn(BaseModel):
    job_id: UUID
    steps: str | None = Field(None, description="Semântica --manual-steps: '3' ou '3,4-6'")
    dates_pattern: list[str] = Field(..., min_length=1, description="Data(s)-alvo")
    confirm_target_dates: bool = Field(
        ..., description="Confirmação explícita — obrigatória, dupla se upload_remote em PROD"
    )
    confirm_upload_remote: bool = Field(
        False, description="Segunda confirmação — exigida quando o contrato tem upload_remote em PROD"
    )
    no_mail: bool = False
    justification: str = Field(..., min_length=3)
    idempotency_key: str | None = None


class ExecutionOut(BaseModel):
    id: UUID
    job_id: UUID
    trigger: str
    requested_steps: list[str]
    dates_pattern: list[str]
    no_mail: bool
    status: str
    result: str | None
    exit_code: int | None
    requested_by: str
    justification: str | None
    started_at: datetime | None
    ended_at: datetime | None
    log_link: str | None

    model_config = {"from_attributes": True}


class AuditEventOut(BaseModel):
    id: UUID
    actor: str
    action: str
    target_type: str
    target_id: str | None
    payload: dict
    source: str
    occurred_at: datetime

    model_config = {"from_attributes": True}


class ErrorOut(BaseModel):
    detail: str
