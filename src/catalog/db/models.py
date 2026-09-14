"""Modelo de dados do catálogo (ver `docs/modelo-de-dados.md`).

Três desvios deliberados do documento, todos aditivos e todos motivados pelo
inventário real de 09/2026:

1. `schedule`/`status` saem de `job` para **`job_schedule`**. No parque, 56
   wrappers aparecem em mais de uma linha de crontab: 527 jobs geram 597
   agendas. Um relatório trimestral tem 4 linhas (jan/abr/jul/out) e é UM job;
   com a agenda dentro de `job`, viraria quatro jobs duplicados. Também é o que
   a Fase 2 precisa: um schedule gerenciado por entrada ativa.

2. `connection_alias` ganha `host`, `username`, `port`, `region`,
   `auth_method`, `key_path`, `is_external` e `status`. Sem isso não é possível
   nem gerar o relatório de migração ao vault: 68 dos 106 aliases compartilham
   a mesma chave SSH, e é `key_path` que revela isso.

3. `job_connection_alias` liga job a alias. Existem aliases cujo nome sugere um
   cliente e cuja credencial é de outro; sem essa tabela não há como responder
   "quais jobs mandam arquivo por este alias" antes de um `upload_remote`.

Imutabilidade (`audit_event`, `job_contract_version`, `job_revision`) é imposta
no banco — trigger + REVOKE na migration — e não por convenção de código.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

SCHEMA = "catalog"

# Nomenclatura explícita: migration e modelo têm de gerar o mesmo nome de
# constraint, senão o teste de drift acusa diferença a cada autogenerate.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(schema=SCHEMA, naming_convention=NAMING_CONVENTION)


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False, default="system")


# ---------------------------------------------------------------------------
# Catálogo
# ---------------------------------------------------------------------------
class Job(Base, TimestampMixin):
    """Unidade do catálogo: o que executar (wrapper + contrato)."""

    __tablename__ = "job"

    id: Mapped[uuid.UUID] = _uuid_pk()
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    process_name: Mapped[str] = mapped_column(String(255), nullable=False)
    wrapper_path: Mapped[str | None] = mapped_column(Text)
    contract_path: Mapped[str | None] = mapped_column(Text)

    # Dimensões de escopo (RBAC, dashboards)
    domain: Mapped[str | None] = mapped_column(String(64))
    environment: Mapped[str | None] = mapped_column(String(16))
    client_code: Mapped[str | None] = mapped_column(String(64))
    client_name: Mapped[str | None] = mapped_column(String(255))
    country_codes: Mapped[list[str]] = mapped_column(
        ARRAY(String(8)), nullable=False, server_default=text("'{}'::varchar[]")
    )

    # `kind` separa job de contrato de rotina de manutenção (clean_logs.sh etc.),
    # que o crontab mistura e que sujaria as métricas do parque.
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="contract_job")

    # Base para alertas de atraso/ausência (Fase 3). Sem fonte no legado:
    # entram nulos e são preenchidos por curadoria.
    criticality: Mapped[str | None] = mapped_column(String(32))
    sla: Mapped[str | None] = mapped_column(String(64))
    owner: Mapped[str | None] = mapped_column(String(255))

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    status_reason: Mapped[str | None] = mapped_column(Text)

    current_contract_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.job_contract_version.id", use_alter=True)
    )

    flags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False, default="system")

    schedules: Mapped[list[JobSchedule]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    contract_versions: Mapped[list[JobContractVersion]] = relationship(
        back_populates="job", foreign_keys="JobContractVersion.job_id"
    )

    __table_args__ = (
        CheckConstraint(
            "kind in ('contract_job','script_task')", name="kind_valido"
        ),
        CheckConstraint(
            "status in ('active','disabled','on_demand','orphan','broken')", name="status_valido"
        ),
        CheckConstraint(
            "environment is null or environment in ('PROD','UAT','TEST','DEV')",
            name="environment_valido",
        ),
        # contract_path é nulo em job sem contrato resolvido; NULL não colide em
        # UNIQUE no Postgres, então a chave natural usa coalesce.
        Index(
            "uq_job_host_wrapper_contract",
            "host",
            "wrapper_path",
            text("coalesce(contract_path, '')"),
            unique=True,
        ),
        Index("ix_job_domain_environment_status", "domain", "environment", "status"),
        Index("ix_job_client_code", "client_code"),
        Index("ix_job_country_codes", "country_codes", postgresql_using="gin"),
        {"schema": SCHEMA},
    )


class JobSchedule(Base, TimestampMixin):
    """Uma entrada de agendamento do job. `schedule_expr` nulo = on-demand."""

    __tablename__ = "job_schedule"

    id: Mapped[uuid.UUID] = _uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.job.id", ondelete="CASCADE"), nullable=False
    )

    schedule_expr: Mapped[str | None] = mapped_column(String(128))
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status_reason: Mapped[str | None] = mapped_column(Text)

    # Preparação da Fase 2: o scheduler gerenciado exige política de catch-up
    # explícita. Nasce nula porque o cron não tem esse conceito.
    catchup_policy: Mapped[str | None] = mapped_column(String(32))

    # Parâmetros hoje hardcoded no wrapper; viram campos da API de execução.
    manual_steps: Mapped[str | None] = mapped_column(String(128))
    dates_pattern: Mapped[str | None] = mapped_column(String(255))
    no_mail: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    cron_source: Mapped[str | None] = mapped_column(String(255))
    cron_lineno: Mapped[int | None] = mapped_column(Integer)
    log_path: Mapped[str | None] = mapped_column(Text)
    raw_line: Mapped[str | None] = mapped_column(Text)

    job: Mapped[Job] = relationship(back_populates="schedules")

    __table_args__ = (
        CheckConstraint(
            "catchup_policy is null or catchup_policy in ('skip','run_once','run_all')",
            name="catchup_policy_valida",
        ),
        UniqueConstraint("job_id", "cron_source", "cron_lineno", name="uq_job_schedule_origem"),
        Index("ix_job_schedule_enabled", "enabled"),
        {"schema": SCHEMA},
    )


class JobContractVersion(Base, TimestampMixin):
    """Versão do contrato JSON. APPEND-ONLY (trigger + REVOKE na migration)."""

    __tablename__ = "job_contract_version"

    id: Mapped[uuid.UUID] = _uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.job.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[str | None] = mapped_column(String(32))
    contract: Mapped[dict] = mapped_column(JSONB, nullable=False)
    contract_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_bytes: Mapped[int | None] = mapped_column(Integer)
    steps_count: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="seed")

    # Veredito do schema NO MOMENTO DA ESCRITA. Fica na versão, não no job:
    # o contrato é imutável, então o veredito também é — revalidar o parque
    # depois de mudar as regras gera versões novas, não reescreve o passado.
    validation_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="unknown", server_default="unknown", index=True
    )
    validation: Mapped[dict | None] = mapped_column(JSONB)

    job: Mapped[Job] = relationship(back_populates="contract_versions", foreign_keys=[job_id])

    __table_args__ = (
        CheckConstraint("source in ('seed','import','api','reconciliation')", name="source_valida"),
        CheckConstraint(
            "validation_status in ('valid','valid_with_warnings','invalid','unknown')",
            name="validation_status_valido",
        ),
        UniqueConstraint("job_id", "version", name="uq_job_contract_version_job_id_version"),
        UniqueConstraint("job_id", "contract_hash", name="uq_job_contract_version_job_id_hash"),
        {"schema": SCHEMA},
    )


class JobRevision(Base, TimestampMixin):
    """Snapshot dos metadados do job. APPEND-ONLY. Base dos diffs consultáveis."""

    __tablename__ = "job_revision"

    id: Mapped[uuid.UUID] = _uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.job.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    diff: Mapped[dict | None] = mapped_column(JSONB)
    audit_event_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    __table_args__ = (
        UniqueConstraint("job_id", "version", name="uq_job_revision_job_id_version"),
        {"schema": SCHEMA},
    )


class JobConnectionAlias(Base):
    """Alias de conexão citado pelo contrato do job."""

    __tablename__ = "job_connection_alias"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.job.id", ondelete="CASCADE"), primary_key=True
    )
    alias_name: Mapped[str] = mapped_column(String(255), primary_key=True)
    # Alias citado no contrato e ausente do connections.json: job que falha na
    # resolução de credencial. É finding, não silêncio.
    declared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    usages: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        Index("ix_job_connection_alias_alias_name", "alias_name"),
        {"schema": SCHEMA},
    )


# ---------------------------------------------------------------------------
# Auditoria — append-only, particionada por mês
# ---------------------------------------------------------------------------
class AuditEvent(Base):
    """Trilha imutável. Cobre catálogo, RBAC e execução (`docs/modelo-de-dados.md`).

    A chave primária inclui `occurred_at` porque a tabela é particionada por
    range nessa coluna — exigência do Postgres.
    """

    __tablename__ = "audit_event"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True, server_default=func.now(), nullable=False
    )
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(255))
    payload: Mapped[dict | None] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        CheckConstraint("source in ('ui','api','cli','job','ssh')", name="source_valida"),
        Index("ix_audit_event_actor_occurred_at", "actor", "occurred_at"),
        Index("ix_audit_event_target", "target_type", "target_id"),
        {"schema": SCHEMA, "postgresql_partition_by": "RANGE (occurred_at)"},
    )


# ---------------------------------------------------------------------------
# Aliases de conexão — só metadados; segredo fica no vault
# ---------------------------------------------------------------------------
class ConnectionAlias(Base, TimestampMixin):
    __tablename__ = "connection_alias"

    name: Mapped[str] = mapped_column(String(255), primary_key=True)
    type: Mapped[str | None] = mapped_column(String(32))
    host: Mapped[str | None] = mapped_column(String(255))
    username: Mapped[str | None] = mapped_column(String(255))
    port: Mapped[int | None] = mapped_column(Integer)
    region: Mapped[str | None] = mapped_column(String(32))

    # `key_path` é o campo que revela a chave compartilhada por 68 aliases.
    auth_method: Mapped[str | None] = mapped_column(String(16))
    key_path: Mapped[str | None] = mapped_column(Text)
    is_external: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    environment: Mapped[str | None] = mapped_column(String(16))

    vault_reference: Mapped[str | None] = mapped_column(Text)
    owners: Mapped[list[str]] = mapped_column(
        ARRAY(String(255)), nullable=False, server_default=text("'{}'::varchar[]")
    )
    validity: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ok")
    reason: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "auth_method is null or auth_method in ('key','password','iam','none')",
            name="auth_method_valido",
        ),
        CheckConstraint(
            "status in ('ok','broken','redundant','retired')", name="status_valido"
        ),
        {"schema": SCHEMA},
    )


# ---------------------------------------------------------------------------
# Snapshot do crontab e reconciliação (requisito 5 do SPEC)
# ---------------------------------------------------------------------------
class CrontabSnapshot(Base):
    __tablename__ = "crontab_snapshot"

    id: Mapped[uuid.UUID] = _uuid_pk()
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    collected_by: Mapped[str | None] = mapped_column(String(255))
    collector_version: Mapped[str | None] = mapped_column(String(32))
    timezone: Mapped[str | None] = mapped_column(String(64))
    framework_root: Mapped[str | None] = mapped_column(Text)
    total_lines: Mapped[int | None] = mapped_column(Integer)
    bundle_sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    entries: Mapped[list[CrontabEntryRow]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("host", "captured_at", name="uq_crontab_snapshot_host_captured_at"),
        {"schema": SCHEMA},
    )


class CrontabEntryRow(Base):
    """Linha bruta do crontab, preservada como coletada. Nada é descartado."""

    __tablename__ = "crontab_entry"

    id: Mapped[uuid.UUID] = _uuid_pk()
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.crontab_snapshot.id", ondelete="CASCADE"),
        nullable=False,
    )
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    lineno: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    schedule_expr: Mapped[str | None] = mapped_column(String(128))
    command: Mapped[str | None] = mapped_column(Text)
    script_path: Mapped[str | None] = mapped_column(Text)
    log_path: Mapped[str | None] = mapped_column(Text)
    status_reason: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[str] = mapped_column(Text, nullable=False)
    flags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )

    snapshot: Mapped[CrontabSnapshot] = relationship(back_populates="entries")

    __table_args__ = (
        UniqueConstraint("snapshot_id", "source", "lineno", name="uq_crontab_entry_origem"),
        {"schema": SCHEMA},
    )


class ReconciliationRun(Base):
    __tablename__ = "reconciliation_run"

    id: Mapped[uuid.UUID] = _uuid_pk()
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.crontab_snapshot.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[dict | None] = mapped_column(JSONB)
    triggered_by: Mapped[str] = mapped_column(String(255), nullable=False, default="system")

    findings: Mapped[list[ReconciliationFinding]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )

    __table_args__ = ({"schema": SCHEMA},)


class ReconciliationFinding(Base):
    """Divergência. O critério de aceite da fase é 'sem divergência NÃO
    EXPLICADA', então `status`/`explanation` são parte do modelo, não relatório.
    """

    __tablename__ = "reconciliation_finding"

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.reconciliation_run.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.job.id", ondelete="SET NULL")
    )

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    explanation: Mapped[str | None] = mapped_column(Text)
    explained_by: Mapped[str | None] = mapped_column(String(255))
    explained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    run: Mapped[ReconciliationRun] = relationship(back_populates="findings")

    __table_args__ = (
        CheckConstraint("severity in ('erro','aviso','info')", name="severity_valida"),
        CheckConstraint("status in ('open','explained','resolved')", name="status_valido"),
        Index("ix_reconciliation_finding_kind_status", "kind", "status"),
        Index("ix_reconciliation_finding_fingerprint", "fingerprint"),
        {"schema": SCHEMA},
    )


APPEND_ONLY_TABLES = ("audit_event", "job_contract_version", "job_revision")


# ---------------------------------------------------------------------------
# Mudança de agendamento (Fase 1: aplicação manual, verificação automática)
# ---------------------------------------------------------------------------
class CrontabChangeRequest(Base, TimestampMixin):
    """Intenção de mudar o estado de agendamento de um job.

    Existe porque, sem ela, a reconciliação só sabe dizer `catálogo ≠ crontab` —
    e não sabe se aquilo é uma mudança em andamento ou alguém editando o crontab
    na mão. São os dois casos que exigem resposta oposta: um é esperado, o outro
    é alarme.

    Na Fase 1 a plataforma **não escreve no crontab** (invariante 2: não tocar no
    plano de execução; um bug num arquivo de 597 linhas editadas à mão é
    catastrófico, e o crontab ainda tem entradas fora do catálogo). O ciclo é
    aplicação manual + verificação automática:

        pending  --(operador aplica a linha-alvo)-->  applied
        pending/applied  --(reconciliação detecta o estado desejado)-->  verified

    `cancelled` é desistência; `expired` é pending além do SLA — que importa
    porque desabilitar no catálogo NÃO para o cron: até alguém aplicar, o job
    continua disparando.

    Na Fase 2 o executor do apply deixa de ser humano e vira pipeline; a máquina
    de estados sobrevive inalterada.
    """

    __tablename__ = "crontab_change_request"

    id: Mapped[uuid.UUID] = _uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.job.id", ondelete="RESTRICT"), nullable=False
    )
    host: Mapped[str] = mapped_column(String(255), nullable=False)

    desired_status: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    # A linha-alvo, não um diff do arquivo: um diff calculado no PATCH pode não
    # aplicar mais quando o operador executar. O estado final é estável.
    instruction: Mapped[str | None] = mapped_column(Text)
    marker: Mapped[str] = mapped_column(String(128), nullable=False)

    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    requested_by: Mapped[str] = mapped_column(String(255), nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    applied_by: Mapped[str | None] = mapped_column(String(255))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Preenchido pela reconciliação, não pelo operador: exigir que ele volte à
    # UI dizer "apliquei" é o passo que na prática ninguém faz.
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.crontab_snapshot.id", ondelete="SET NULL")
    )

    job: Mapped[Job] = relationship()

    __table_args__ = (
        CheckConstraint(
            "state in ('pending','applied','verified','cancelled','expired')",
            name="state_valido",
        ),
        CheckConstraint(
            "desired_status in ('active','disabled')", name="desired_status_valido"
        ),
        Index("ix_crontab_change_request_state", "state"),
        Index("ix_crontab_change_request_job_id", "job_id"),
        {"schema": SCHEMA},
    )

    @property
    def is_open(self) -> bool:
        return self.state in ("pending", "applied")


# ---------------------------------------------------------------------------
# Autorização
# ---------------------------------------------------------------------------
class RoleBinding(Base, TimestampMixin):
    """Vínculo entre um sujeito do Entra ID e uma role, com escopo.

    O escopo mora aqui, e não em grupos do Entra, porque precisa ser consultável
    e auditável junto do catálogo: a pergunta operacional é "quem pode executar
    este job", e ela se responde por domínio/ambiente/host do próprio job.

    `NULL` em qualquer dimensão de escopo significa **todas** — `scope_domain`
    nulo é "todos os domínios". É o default deliberado: uma role sem escopo é
    ampla, então conceder escopo é sempre restringir, nunca ampliar.
    """

    __tablename__ = "role_binding"

    id: Mapped[uuid.UUID] = _uuid_pk()
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(16), nullable=False, default="user")
    role: Mapped[str] = mapped_column(String(64), nullable=False)

    scope_domain: Mapped[str | None] = mapped_column(String(64))
    scope_environment: Mapped[str | None] = mapped_column(String(16))
    scope_host: Mapped[str | None] = mapped_column(String(255))

    granted_by: Mapped[str] = mapped_column(String(255), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by: Mapped[str | None] = mapped_column(String(255))

    __table_args__ = (
        CheckConstraint(
            "role in ('batch.viewer','batch.operator','batch.operator-prod','batch.admin')",
            name="role_valida",
        ),
        CheckConstraint("subject_type in ('user','group')", name="subject_type_valido"),
        CheckConstraint(
            "scope_environment is null or scope_environment in ('PROD','UAT','TEST','DEV')",
            name="scope_environment_valido",
        ),
        Index("ix_role_binding_subject", "subject"),
        Index("ix_role_binding_role", "role"),
        {"schema": SCHEMA},
    )

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


# ---------------------------------------------------------------------------
# Execução
# ---------------------------------------------------------------------------
class Execution(Base, TimestampMixin):
    """Um registro por invocação — inclusive execução do legado disparada pela
    API na Fase 1 (invariante 3: execução como dado).

    `id` É o `execution_id` propagado aos logs. Na Fase 1 o `main.sh` não aceita
    identificador de execução e calcula o próprio caminho de log a partir da
    data; quem nomeia o arquivo por `execution_id` é o wrapper do `command=`,
    que controla o redirecionamento. `log_link` guarda essa correlação.
    """

    __tablename__ = "execution"

    id: Mapped[uuid.UUID] = _uuid_pk()
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.job.id", ondelete="RESTRICT"), nullable=False
    )
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)

    requested_steps: Mapped[list[str]] = mapped_column(
        ARRAY(String(16)), nullable=False, server_default=text("'{}'::varchar[]")
    )
    dates_pattern: Mapped[list[str]] = mapped_column(
        ARRAY(String(32)), nullable=False, server_default=text("'{}'::varchar[]")
    )
    no_mail: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    result: Mapped[str | None] = mapped_column(String(16))
    exit_code: Mapped[int | None] = mapped_column(Integer)

    requested_by: Mapped[str] = mapped_column(String(255), nullable=False)
    justification: Mapped[str | None] = mapped_column(Text)
    # Chave de idempotência: reenvio da mesma requisição não dispara de novo.
    # É a salvaguarda do invariante 7 contra reenvio acidental a cliente.
    idempotency_key: Mapped[str | None] = mapped_column(String(128))

    backend: Mapped[str] = mapped_column(String(32), nullable=False, default="ssh")
    host: Mapped[str | None] = mapped_column(String(255))
    worker: Mapped[str | None] = mapped_column(String(255))

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    log_link: Mapped[str | None] = mapped_column(Text)

    job: Mapped[Job] = relationship()

    __table_args__ = (
        CheckConstraint(
            "trigger in ('schedule','manual','reprocess','event','dependency')",
            name="trigger_valido",
        ),
        CheckConstraint(
            "status in ('queued','running','succeeded','failed','cancelled')",
            name="status_valido",
        ),
        CheckConstraint(
            "result is null or result in ('success','failure','partial')", name="result_valido"
        ),
        UniqueConstraint("idempotency_key", name="uq_execution_idempotency_key"),
        Index("ix_execution_job_id_started_at", "job_id", "started_at"),
        Index("ix_execution_status", "status"),
        {"schema": SCHEMA},
    )


__all__ = [
    "APPEND_ONLY_TABLES",
    "AuditEvent",
    "Base",
    "ConnectionAlias",
    "CrontabChangeRequest",
    "CrontabEntryRow",
    "CrontabSnapshot",
    "Job",
    "JobConnectionAlias",
    "JobContractVersion",
    "JobRevision",
    "JobSchedule",
    "ReconciliationFinding",
    "Execution",
    "ReconciliationRun",
    "RoleBinding",
    "SCHEMA",
]
