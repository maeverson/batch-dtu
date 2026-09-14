"""Persistência do catálogo — do `BuildResult` para as tabelas.

Regras que a implementação tem de sustentar, e que os testes verificam:

* **Idempotência.** Rodar o import duas vezes sobre o mesmo pacote não cria
  job duplicado, nem versão de contrato repetida, nem revisão sem mudança, nem
  evento de auditoria falso. É o que permite reexecutar a carga com segurança.
* **Auditoria na mesma transação.** Toda escrita de catálogo gera `audit_event`
  no mesmo `commit` — sem isso existe uma janela em que a mudança está no banco
  e a trilha não (invariante 3).
* **Nada é descartado.** As 1352 linhas de crontab entram em `crontab_entry`
  como coletadas, inclusive comentário de documentação e linha em branco.
* **Findings com fingerprint estável.** A explicação dada a uma divergência
  sobrevive à próxima reconciliação; sem isso o critério "sem divergência não
  explicada" seria impossível de fechar.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..build import BuildResult, JobRecord
from ..contract_schema import Policy, assert_valid
from .models import (
    AuditEvent,
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
)

# Campos de metadado do job cuja mudança gera revisão + auditoria.
TRACKED_FIELDS = (
    "domain",
    "environment",
    "client_code",
    "client_name",
    "country_codes",
    "contract_path",
    "status",
    "status_reason",
    "kind",
    "flags",
)


@dataclass
class LoadReport:
    host: str
    dry_run: bool = False
    jobs_created: int = 0
    jobs_updated: int = 0
    jobs_unchanged: int = 0
    schedules_created: int = 0
    schedules_updated: int = 0
    contract_versions_created: int = 0
    contracts_invalid: int = 0
    aliases_upserted: int = 0
    cron_entries_stored: int = 0
    findings_stored: int = 0
    findings_carried: int = 0
    audit_events: int = 0
    snapshot_id: str | None = None
    reconciliation_run_id: str | None = None
    notes: list[str] = field(default_factory=list)


def load_catalog(
    result: BuildResult,
    session: Session,
    *,
    actor: str,
    source: str = "cli",
    dry_run: bool = False,
    bundle_sha256: str | None = None,
    policy: Policy = Policy.LEGACY,
) -> LoadReport:
    """Carrega o resultado do build no catálogo. Uma transação, um relatório.

    `policy` decide o que fazer com contrato que falha no schema. O default é
    `LEGACY` porque o import do parque tem de conseguir registrar o que existe
    — inclusive o que está quebrado. `STRICT` levanta `ContractInvalid` e
    aborta a transação inteira; é a política da escrita nova.
    """
    report = LoadReport(host=result.host, dry_run=dry_run)
    now = datetime.now(timezone.utc)

    snapshot = _store_snapshot(result, session, report, now, bundle_sha256)
    _upsert_aliases(result, session, report, actor, now)

    grouped = _group_jobs(result)
    for (wrapper_path, contract_path), records in grouped.items():
        _persist_job(result, session, report, actor, source, now,
                     wrapper_path, contract_path, records, policy)

    _store_reconciliation(result, session, report, snapshot, actor, now)

    _audit(
        session, report, actor=actor, source=source, action="catalog.import",
        target_type="host", target_id=result.host,
        payload={
            "framework_root": result.framework_root,
            "timezone": result.timezone,
            "jobs": len(grouped),
            "schedules": len(result.jobs),
            "jobs_created": report.jobs_created,
            "jobs_updated": report.jobs_updated,
            "contract_versions_created": report.contract_versions_created,
            "contracts_invalid": report.contracts_invalid,
            "validation_policy": policy.value,
            "findings": len(result.findings),
            "dry_run": dry_run,
        },
        occurred_at=now,
    )

    if dry_run:
        session.rollback()
        report.notes.append("dry-run: transação revertida, nada foi persistido")
    else:
        session.flush()

    return report


# ---------------------------------------------------------------------------
# Snapshot do crontab
# ---------------------------------------------------------------------------
def _store_snapshot(
    result: BuildResult,
    session: Session,
    report: LoadReport,
    now: datetime,
    bundle_sha256: str | None,
) -> CrontabSnapshot:
    captured = _parse_collected_at(result.host_info.get("collected_at_utc")) or now
    existing = session.scalar(
        select(CrontabSnapshot).where(
            CrontabSnapshot.host == result.host, CrontabSnapshot.captured_at == captured
        )
    )
    if existing is not None:
        report.snapshot_id = str(existing.id)
        report.notes.append("snapshot já registrado; linhas de crontab preservadas")
        return existing

    snapshot = CrontabSnapshot(
        host=result.host,
        captured_at=captured,
        collected_by=result.host_info.get("collected_by"),
        collector_version=result.host_info.get("collector"),
        timezone=result.timezone,
        framework_root=result.framework_root,
        total_lines=len(result.cron_entries),
        bundle_sha256=bundle_sha256,
    )
    session.add(snapshot)
    session.flush()
    report.snapshot_id = str(snapshot.id)

    for entry in result.cron_entries:
        session.add(
            CrontabEntryRow(
                snapshot_id=snapshot.id,
                source=entry.source,
                lineno=entry.lineno,
                kind=entry.kind.value,
                enabled=entry.enabled,
                schedule_expr=entry.schedule,
                command=entry.command,
                script_path=entry.script_path,
                log_path=entry.log_path,
                status_reason=entry.status_reason,
                raw=entry.raw,
                flags=list(entry.flags),
            )
        )
        report.cron_entries_stored += 1
    session.flush()
    return snapshot


# ---------------------------------------------------------------------------
# Aliases de conexão
# ---------------------------------------------------------------------------
def _upsert_aliases(
    result: BuildResult, session: Session, report: LoadReport, actor: str, now: datetime
) -> None:
    for record in result.alias_metadata:
        name = record.get("name")
        if not name:
            continue
        alias = session.get(ConnectionAlias, name)
        port = record.get("port")
        values = {
            "type": record.get("type"),
            "host": record.get("host"),
            "username": record.get("username"),
            "port": int(port) if port and str(port).isdigit() else None,
            "region": record.get("region"),
            "auth_method": record.get("auth_method"),
            "key_path": record.get("key_path"),
            "is_external": _is_external(record.get("host")),
            "status": _alias_status(record),
        }
        if alias is None:
            session.add(ConnectionAlias(name=name, created_by=actor, **values))
            report.aliases_upserted += 1
        else:
            changed = {k: v for k, v in values.items() if getattr(alias, k) != v}
            if changed:
                for key, value in changed.items():
                    setattr(alias, key, value)
                report.aliases_upserted += 1
                _audit(
                    session, report, actor=actor, source="cli", action="alias.update",
                    target_type="connection_alias", target_id=name,
                    payload={"changed": list(changed)}, occurred_at=now,
                )
    session.flush()


def _alias_status(record: dict) -> str:
    """Alias sem credencial e sem IAM não resolve em runtime: nasce `broken`."""
    auth = record.get("auth_method")
    if auth in (None, "none"):
        return "broken"
    if auth == "key" and not record.get("key_path"):
        return "broken"
    return "ok"


def _is_external(host: str | None) -> bool:
    if not host:
        return False
    import ipaddress

    try:
        return not ipaddress.ip_address(host).is_private
    except ValueError:
        return "." in host and not host.endswith(".novopayment.net")


# ---------------------------------------------------------------------------
# Jobs, agendas, versões de contrato
# ---------------------------------------------------------------------------
def _group_jobs(result: BuildResult) -> dict[tuple[str | None, str | None], list[JobRecord]]:
    """Um job por (wrapper, contrato); cada linha de cron é uma agenda dele."""
    grouped: dict[tuple[str | None, str | None], list[JobRecord]] = {}
    for record in result.jobs:
        grouped.setdefault((record.wrapper_path, record.contract_path), []).append(record)
    return grouped


def _persist_job(
    result: BuildResult,
    session: Session,
    report: LoadReport,
    actor: str,
    source: str,
    now: datetime,
    wrapper_path: str | None,
    contract_path: str | None,
    records: list[JobRecord],
    policy: Policy = Policy.LEGACY,
) -> None:
    reference = records[0]
    desired = {
        "domain": reference.domain,
        "environment": reference.environment,
        "client_code": reference.client_code,
        "client_name": reference.client_name,
        "country_codes": list(reference.country_codes),
        "contract_path": contract_path,
        "status": _job_status(records),
        "status_reason": next((r.status_reason for r in records if r.status_reason), None),
        "kind": "contract_job" if contract_path else "script_task",
        "flags": sorted({flag for r in records for flag in r.flags}),
    }

    job = session.scalar(
        select(Job).where(
            Job.host == result.host,
            Job.wrapper_path == wrapper_path,
            Job.contract_path == contract_path,
        )
    )

    if job is None:
        job = Job(
            host=result.host,
            process_name=reference.process_name,
            wrapper_path=wrapper_path,
            created_by=actor,
            updated_by=actor,
            **desired,
        )
        session.add(job)
        session.flush()
        report.jobs_created += 1
        _audit(
            session, report, actor=actor, source=source, action="job.create",
            target_type="job", target_id=str(job.id),
            payload={"process_name": job.process_name, **_jsonable(desired)}, occurred_at=now,
        )
        _revision(session, job, desired, None, actor, now)
    else:
        changed = {
            key: value for key, value in desired.items() if getattr(job, key) != value
        }
        if changed:
            before = {key: getattr(job, key) for key in changed}
            for key, value in desired.items():
                setattr(job, key, value)
            job.updated_by = actor
            report.jobs_updated += 1
            _audit(
                session, report, actor=actor, source=source, action="job.update",
                target_type="job", target_id=str(job.id),
                payload={"antes": _jsonable(before), "depois": _jsonable(changed)},
                occurred_at=now,
            )
            _revision(session, job, desired, before, actor, now)
        else:
            report.jobs_unchanged += 1

    _persist_contract_version(result, session, report, job, reference, actor, source, now, policy)
    _persist_schedules(session, report, job, records, result, actor, source, now)
    _persist_aliases_of_job(session, job, records, result)


def _job_status(records: list[JobRecord]) -> str:
    if any("contrato-ausente" in r.flags for r in records):
        return "broken"
    if any(r.enabled for r in records):
        return "active"
    if all(r.schedule is None for r in records):
        return "on_demand"
    return "disabled"


def _persist_contract_version(
    result: BuildResult,
    session: Session,
    report: LoadReport,
    job: Job,
    reference: JobRecord,
    actor: str,
    source: str,
    now: datetime,
    policy: Policy = Policy.LEGACY,
) -> None:
    if not reference.contract_path or not reference.contract_hash:
        return

    already = session.scalar(
        select(JobContractVersion).where(
            JobContractVersion.job_id == job.id,
            JobContractVersion.contract_hash == reference.contract_hash,
        )
    )
    if already is not None:
        if job.current_contract_version_id != already.id:
            job.current_contract_version_id = already.id
        return

    local = result.contracts.get(reference.contract_path)
    contract = _read_contract(local)

    # Validação NA ESCRITA: acontece antes da versão existir, e o veredito é
    # gravado junto dela. Sob STRICT isto levanta e a transação inteira cai.
    validation = assert_valid(contract, policy)
    if not validation.valid:
        report.contracts_invalid += 1

    last = session.scalar(
        select(JobContractVersion.version)
        .where(JobContractVersion.job_id == job.id)
        .order_by(JobContractVersion.version.desc())
        .limit(1)
    )
    version = JobContractVersion(
        job_id=job.id,
        version=(last or 0) + 1,
        schema_version=reference.schema_version,
        contract=contract,
        contract_hash=reference.contract_hash,
        contract_bytes=reference.contract_bytes,
        steps_count=reference.steps_count,
        source="seed" if last is None else "import",
        created_by=actor,
        validation_status=validation.status,
        validation=validation.as_dict(),
    )
    session.add(version)
    session.flush()
    job.current_contract_version_id = version.id
    report.contract_versions_created += 1
    _audit(
        session, report, actor=actor, source=source, action="job.contract_version",
        target_type="job", target_id=str(job.id),
        payload={
            "version": version.version,
            "contract_hash": version.contract_hash,
            "schema_version": version.schema_version,
            "steps": version.steps_count,
            "validation_status": version.validation_status,
            "violations": len(validation.violations),
        },
        occurred_at=now,
    )


def _read_contract(local: Path | None) -> dict:
    if local is None or not local.is_file():
        return {}
    try:
        data = json.loads(local.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        # Contrato inválido é preservado como texto: o catálogo registra o que
        # existe, não o que deveria existir.
        return {"__invalid_json__": str(exc), "__raw__": local.read_text(errors="replace")[:20000]}
    return data if isinstance(data, dict) else {"__root_is_list__": data}


def _persist_schedules(
    session: Session,
    report: LoadReport,
    job: Job,
    records: list[JobRecord],
    result: BuildResult,
    actor: str,
    source: str,
    now: datetime,
) -> None:
    raw_by_origin = {
        (entry.source, entry.lineno): entry.raw for entry in result.cron_entries
    }
    for record in records:
        existing = session.scalar(
            select(JobSchedule).where(
                JobSchedule.job_id == job.id,
                JobSchedule.cron_source == record.cron_source,
                JobSchedule.cron_lineno == record.cron_lineno,
            )
        )
        values = {
            "schedule_expr": record.schedule,
            "timezone": record.timezone or result.timezone or "UTC",
            "enabled": record.enabled,
            "status_reason": record.status_reason,
            "manual_steps": record.manual_steps,
            "dates_pattern": record.dates_pattern,
            "no_mail": record.no_mail,
            "log_path": record.log_path,
            "raw_line": raw_by_origin.get((record.cron_source, record.cron_lineno)),
        }
        if existing is None:
            session.add(
                JobSchedule(
                    job_id=job.id,
                    cron_source=record.cron_source,
                    cron_lineno=record.cron_lineno,
                    created_by=actor,
                    **values,
                )
            )
            report.schedules_created += 1
        else:
            changed = {k: v for k, v in values.items() if getattr(existing, k) != v}
            if changed:
                for key, value in changed.items():
                    setattr(existing, key, value)
                report.schedules_updated += 1
                _audit(
                    session, report, actor=actor, source=source, action="job.schedule_update",
                    target_type="job", target_id=str(job.id),
                    payload={"cron": f"{record.cron_source}:{record.cron_lineno}",
                             "changed": _jsonable(changed)},
                    occurred_at=now,
                )
    session.flush()


def _persist_aliases_of_job(
    session: Session, job: Job, records: list[JobRecord], result: BuildResult
) -> None:
    counts: dict[str, int] = {}
    for record in records:
        for alias in record.connection_aliases:
            counts[alias] = counts.get(alias, 0) + 1

    for alias, usages in counts.items():
        row = session.get(JobConnectionAlias, {"job_id": job.id, "alias_name": alias})
        declared = alias in result.aliases_declared
        if row is None:
            session.add(
                JobConnectionAlias(
                    job_id=job.id, alias_name=alias, declared=declared, usages=usages
                )
            )
        else:
            row.declared = declared
            row.usages = usages
    session.flush()


def _revision(
    session: Session,
    job: Job,
    desired: dict,
    before: dict | None,
    actor: str,
    now: datetime,
) -> None:
    last = session.scalar(
        select(JobRevision.version)
        .where(JobRevision.job_id == job.id)
        .order_by(JobRevision.version.desc())
        .limit(1)
    )
    session.add(
        JobRevision(
            job_id=job.id,
            version=(last or 0) + 1,
            snapshot=_jsonable({"process_name": job.process_name, "wrapper_path": job.wrapper_path,
                                **desired}),
            diff=_jsonable(before) if before else None,
            created_by=actor,
        )
    )
    session.flush()


# ---------------------------------------------------------------------------
# Reconciliação
# ---------------------------------------------------------------------------
def _store_reconciliation(
    result: BuildResult,
    session: Session,
    report: LoadReport,
    snapshot: CrontabSnapshot,
    actor: str,
    now: datetime,
) -> None:
    run = ReconciliationRun(
        host=result.host,
        snapshot_id=snapshot.id,
        finished_at=now,
        triggered_by=actor,
        summary={
            "jobs": len({(j.wrapper_path, j.contract_path) for j in result.jobs}),
            "schedules": len(result.jobs),
            "contracts": len(result.contracts),
            "orphan_contracts": len(result.orphan_contracts),
            "findings": len(result.findings),
        },
    )
    session.add(run)
    session.flush()
    report.reconciliation_run_id = str(run.id)

    # Explicações dadas em execuções anteriores seguem valendo: a chave é o
    # fingerprint, não o id da run.
    # `populate_existing` porque esta consulta é uma leitura de ESTADO ATUAL:
    # sem ela, um objeto já carregado na sessão devolve os valores antigos e a
    # explicação se perde silenciosamente na reconciliação seguinte.
    explained = {
        row.fingerprint: row
        for row in session.scalars(
            select(ReconciliationFinding)
            .where(ReconciliationFinding.status == "explained")
            .execution_options(populate_existing=True)
        )
    }

    for finding in result.findings:
        fingerprint = _fingerprint(finding.kind, finding.subject, finding.detail)
        previous = explained.get(fingerprint)
        session.add(
            ReconciliationFinding(
                run_id=run.id,
                kind=finding.kind,
                severity=finding.severity.value,
                subject=finding.subject[:4000],
                detail=finding.detail[:4000],
                fingerprint=fingerprint,
                status="explained" if previous else "open",
                explanation=previous.explanation if previous else None,
                explained_by=previous.explained_by if previous else None,
                explained_at=previous.explained_at if previous else None,
            )
        )
        report.findings_stored += 1
        if previous:
            report.findings_carried += 1
    session.flush()


def _fingerprint(kind: str, subject: str, detail: str) -> str:
    return hashlib.sha256(f"{kind}\x00{subject}\x00{detail}".encode()).hexdigest()


# ---------------------------------------------------------------------------
# Auditoria
# ---------------------------------------------------------------------------
def _audit(
    session: Session,
    report: LoadReport,
    *,
    actor: str,
    source: str,
    action: str,
    target_type: str,
    target_id: str | None,
    payload: dict,
    occurred_at: datetime,
) -> None:
    session.add(
        AuditEvent(
            actor=actor,
            action=action,
            target_type=target_type,
            target_id=target_id,
            payload=payload,
            source=source,
            occurred_at=occurred_at,
        )
    )
    report.audit_events += 1


def _jsonable(value):
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _parse_collected_at(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def report_as_dict(report: LoadReport) -> dict:
    return asdict(report)
