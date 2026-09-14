"""Job de reconciliação crontab × catálogo.

Diferente do `load_catalog`: **não escreve catálogo** — job, agenda e contrato
são só lidos. Lê o estado persistido, compara com uma coleta fresca do host e
grava um `reconciliation_run` com as divergências.

A única escrita é o avanço da máquina de estados de `crontab_change_request`
(`verified`, `expired`). Não é exceção à regra, é o oposto dela: o reconciler
não altera o que o catálogo *afirma*, ele registra o que *observou* sobre uma
mudança que já estava pedida. Se ele pudesse mexer no job, consertaria a
divergência que deveria reportar. É o job que responde ao critério de aceite da Etapa 1.1 —
"relatório de reconciliação sem divergências não explicadas" — e por isso ele
distingue divergência ABERTA de divergência EXPLICADA: o fingerprint carrega a
explicação dada na execução anterior.

Cinco classes de divergência, nesta ordem de gravidade:

* `job-fantasma` — o crontab agenda algo que o catálogo não conhece. É o pior
  caso: execução em produção fora de governança (invariante 3).
* `job-sem-entrada-no-crontab` — o catálogo diz ativo, o crontab não agenda.
  Job que ninguém roda e todo mundo acha que roda.
* `agenda-divergente` — mesma origem, expressão/estado diferente. Alguém editou
  o crontab à mão.
* `contrato-divergente` — o arquivo em disco não é a versão corrente do
  catálogo. Contrato mudou sem passar pela plataforma.
* `metadado-divergente` — domínio/cliente/ambiente/status fora de sincronia.

Os achados do build (alias não declarado, contrato inválido, contrato ausente)
entram no mesmo run: para quem opera, é um relatório só.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..build import BuildResult, JobRecord, Severity
from .models import (
    CrontabChangeRequest,
    CrontabSnapshot,
    Job,
    JobContractVersion,
    JobSchedule,
    ReconciliationFinding,
    ReconciliationRun,
)
from .repository import TRACKED_FIELDS, _audit, _fingerprint, _group_jobs, _job_status

# Campos de agenda cuja divergência importa. `log_path` e `raw_line` ficam de
# fora: mudam com o caminho do log sem mudar o que é executado.
SCHEDULE_FIELDS = ("schedule_expr", "enabled", "manual_steps", "dates_pattern", "no_mail")


@dataclass
class Divergence:
    kind: str
    severity: Severity
    subject: str
    detail: str
    job_id: object | None = None


@dataclass
class ReconcileReport:
    host: str
    changes_verified: int = 0
    changes_expired: int = 0
    changes_open: int = 0
    run_id: str | None = None
    jobs_in_catalog: int = 0
    jobs_in_crontab: int = 0
    divergences: list[Divergence] = field(default_factory=list)
    open_count: int = 0
    explained_count: int = 0
    open_errors: int = 0
    audit_events: int = 0

    @property
    def by_kind(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for d in self.divergences:
            out[d.kind] = out.get(d.kind, 0) + 1
        return out


def reconcile(
    result: BuildResult,
    session: Session,
    *,
    actor: str = "system",
    source: str = "cli",
    dry_run: bool = False,
) -> ReconcileReport:
    """Compara a coleta com o catálogo persistido.

    Não altera job/agenda/contrato; só avança change requests já abertas.
    """
    report = ReconcileReport(host=result.host)
    now = datetime.now(timezone.utc)

    catalog_jobs = {
        (job.wrapper_path, job.contract_path): job
        for job in session.scalars(select(Job).where(Job.host == result.host))
    }
    collected = _group_jobs(result)
    report.jobs_in_catalog = len(catalog_jobs)
    report.jobs_in_crontab = len(collected)

    divergences: list[Divergence] = []

    # Mudanças em andamento: sem elas, `catálogo ≠ crontab` é ambíguo entre
    # "mudança aguardando aplicação" e "alguém editou o crontab por fora".
    abertas = {
        req.job_id: req
        for req in session.scalars(
            select(CrontabChangeRequest)
            .where(CrontabChangeRequest.host == result.host,
                   CrontabChangeRequest.state.in_(("pending", "applied")))
            .execution_options(populate_existing=True)
        )
    }

    for key, records in collected.items():
        job = catalog_jobs.get(key)
        if job is None:
            _ghost(key, records, divergences)
            continue
        _diff_metadata(job, records, key, divergences, abertas.get(job.id))
        _diff_contract(job, records, session, divergences)
        _diff_schedules(job, records, session, divergences)

    for key, job in catalog_jobs.items():
        if key in collected:
            continue
        # Job de manutenção nunca teve entrada de contrato; ausência é esperada.
        severity = Severity.ERROR if job.status == "active" else Severity.INFO
        divergences.append(Divergence(
            "job-sem-entrada-no-crontab", severity, job.process_name,
            f"catalogo diz status={job.status} mas a coleta de {result.host} "
            f"nao tem entrada para {job.wrapper_path or job.contract_path}",
            job.id,
        ))

    # Os achados do build são divergências do mesmo relatório: quem opera lê
    # um run, não dois.
    for finding in result.findings:
        divergences.append(Divergence(
            finding.kind, finding.severity, finding.subject, finding.detail))

    ultimo_snapshot = session.scalar(
        select(CrontabSnapshot.id)
        .where(CrontabSnapshot.host == result.host)
        .order_by(CrontabSnapshot.captured_at.desc())
        .limit(1)
    )
    _close_change_requests(
        result, session, report, divergences, abertas, collected, catalog_jobs,
        snapshot_id=ultimo_snapshot, now=now,
    )

    report.divergences = divergences
    _store(result, session, report, divergences, actor, source, now, dry_run)
    return report


def _close_change_requests(
    result: BuildResult,
    session: Session,
    report: ReconcileReport,
    out: list[Divergence],
    abertas: dict,
    collected: dict,
    catalog_jobs: dict,
    *,
    snapshot_id,
    now: datetime,
) -> None:
    """Fecha o loop: verifica o que foi aplicado, expira o que venceu.

    A verificação é por DETECÇÃO, não por declaração do operador — exigir que
    ele volte à UI dizer "apliquei" é o passo que na prática ninguém faz. O
    marcador `#BO:<job>:<change>` na linha é confirmação adicional quando está
    presente, nunca requisito: o que decide é o estado observado bater com o
    desejado.
    """
    observado: dict = {}
    marcadores: dict = {}
    for key, records in collected.items():
        job = catalog_jobs.get(key)
        if job is None:
            continue
        observado[job.id] = _job_status(records)
        for r in records:
            if getattr(r, "bo_change_id", None):
                marcadores[job.id] = r.bo_change_id

    for job_id, req in abertas.items():
        estado = observado.get(job_id)
        if estado is None:
            report.changes_open += 1
            continue

        if estado == req.desired_status:
            req.state = "verified"
            req.verified_at = now
            req.verified_snapshot_id = snapshot_id
            report.changes_verified += 1
            confirmacao = ("marcador presente na linha"
                           if marcadores.get(job_id) == str(req.id)
                           else "sem marcador; estado observado bate com o desejado")
            out.append(Divergence(
                "mudanca-verificada", Severity.INFO,
                catalog_jobs and next(
                    (j.process_name for j in catalog_jobs.values() if j.id == job_id), str(job_id)),
                f"change_request {req.id} aplicada: crontab agora diz "
                f"'{estado}' ({confirmacao})",
                job_id,
            ))
            continue

        # Continua divergente. Vencida?
        if req.expires_at is not None and req.expires_at <= now and req.state == "pending":
            req.state = "expired"
            report.changes_expired += 1
            out.append(Divergence(
                "mudanca-pendente-vencida", Severity.ERROR,
                next((j.process_name for j in catalog_jobs.values() if j.id == job_id),
                     str(job_id)),
                f"change_request {req.id} pedida em {req.requested_at:%Y-%m-%d} e nao aplicada "
                f"ate {req.expires_at:%Y-%m-%d}; o cron NAO parou — o job segue disparando",
                job_id,
            ))
        else:
            report.changes_open += 1

    session.flush()


# ---------------------------------------------------------------------------
# Comparações
# ---------------------------------------------------------------------------
def _ghost(key, records: list[JobRecord], out: list[Divergence]) -> None:
    wrapper_path, contract_path = key
    reference = records[0]
    active = any(r.enabled for r in records)
    origins = ", ".join(
        f"{r.cron_source}:{r.cron_lineno}" for r in records if r.cron_lineno is not None
    )
    out.append(Divergence(
        "job-fantasma", Severity.ERROR if active else Severity.WARNING,
        reference.process_name,
        f"agendado em {origins or 'origem desconhecida'} "
        f"({'ATIVO' if active else 'desabilitado'}) e ausente do catalogo: "
        f"{wrapper_path or contract_path}",
    ))


def _diff_metadata(
    job: Job, records: list[JobRecord], key, out: list[Divergence],
    change: CrontabChangeRequest | None = None,
) -> None:
    reference = records[0]
    _, contract_path = key
    observed = {
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
    for field_name in TRACKED_FIELDS:
        current = getattr(job, field_name)
        if isinstance(current, list):
            current = list(current)
        if current == observed[field_name]:
            continue
        if field_name == "status":
            # Divergência de status tem duas leituras opostas, e a change_request
            # é o que as separa: mudança pedida e ainda não aplicada é esperada;
            # sem request, alguém editou o crontab fora do fluxo.
            if change is not None and change.desired_status == current:
                out.append(Divergence(
                    "mudanca-em-andamento", Severity.INFO, job.process_name,
                    f"change_request {change.id} ({change.state}): catalogo ja diz "
                    f"'{current}', crontab ainda diz '{observed[field_name]}' — "
                    f"o cron so para quando a linha for aplicada",
                    job.id,
                ))
            else:
                out.append(Divergence(
                    "drift-nao-gerenciado", Severity.ERROR, job.process_name,
                    f"status: catalogo={current!r}, coleta={observed[field_name]!r} "
                    "e nenhuma change_request aberta explica a diferenca",
                    job.id,
                ))
            continue

        out.append(Divergence(
            "metadado-divergente", Severity.WARNING, job.process_name,
            f"{field_name}: catalogo={current!r}, coleta={observed[field_name]!r}",
            job.id,
        ))


def _diff_contract(
    job: Job, records: list[JobRecord], session: Session, out: list[Divergence]
) -> None:
    observed_hash = next((r.contract_hash for r in records if r.contract_hash), None)
    if observed_hash is None:
        return
    if job.current_contract_version_id is None:
        out.append(Divergence(
            "contrato-divergente", Severity.WARNING, job.process_name,
            f"contrato em disco (sha256={observed_hash[:12]}) sem versao corrente no catalogo",
            job.id,
        ))
        return
    current = session.get(JobContractVersion, job.current_contract_version_id)
    if current is not None and current.contract_hash != observed_hash:
        out.append(Divergence(
            "contrato-divergente", Severity.ERROR, job.process_name,
            f"disco sha256={observed_hash[:12]} != catalogo v{current.version} "
            f"sha256={current.contract_hash[:12]}",
            job.id,
        ))


def _diff_schedules(
    job: Job, records: list[JobRecord], session: Session, out: list[Divergence]
) -> None:
    stored = {
        (s.cron_source, s.cron_lineno): s
        for s in session.scalars(select(JobSchedule).where(JobSchedule.job_id == job.id))
    }
    observed = {(r.cron_source, r.cron_lineno): r for r in records}

    for origin, record in observed.items():
        schedule = stored.get(origin)
        if schedule is None:
            out.append(Divergence(
                "agenda-divergente", Severity.WARNING, job.process_name,
                f"{origin[0]}:{origin[1]} existe na coleta e nao no catalogo",
                job.id,
            ))
            continue
        for field_name in SCHEDULE_FIELDS:
            current = getattr(schedule, field_name)
            fresh = getattr(record, "schedule" if field_name == "schedule_expr" else field_name)
            if current != fresh:
                out.append(Divergence(
                    "agenda-divergente",
                    Severity.ERROR if field_name in ("schedule_expr", "enabled")
                    else Severity.WARNING,
                    job.process_name,
                    f"{origin[0]}:{origin[1]} {field_name}: "
                    f"catalogo={current!r}, coleta={fresh!r}",
                    job.id,
                ))

    for origin, schedule in stored.items():
        if origin not in observed:
            out.append(Divergence(
                "agenda-divergente",
                Severity.ERROR if schedule.enabled else Severity.INFO,
                job.process_name,
                f"{origin[0]}:{origin[1]} existe no catalogo e sumiu da coleta "
                f"(enabled={schedule.enabled})",
                job.id,
            ))


# ---------------------------------------------------------------------------
# Persistência do run
# ---------------------------------------------------------------------------
def _store(
    result: BuildResult,
    session: Session,
    report: ReconcileReport,
    divergences: list[Divergence],
    actor: str,
    source: str,
    now: datetime,
    dry_run: bool,
) -> None:
    run = ReconciliationRun(
        host=result.host,
        finished_at=now,
        triggered_by=actor,
        summary={
            "mode": "reconcile",
            "jobs_in_catalog": report.jobs_in_catalog,
            "jobs_in_crontab": report.jobs_in_crontab,
            "divergences": len(divergences),
            "by_kind": report.by_kind,
            "dry_run": dry_run,
        },
    )
    session.add(run)
    session.flush()
    report.run_id = str(run.id)

    # A explicação sobrevive ao run: a chave é o fingerprint. Sem isso o
    # critério "sem divergência não explicada" seria inatingível — toda
    # execução recomeçaria do zero.
    explained = {
        row.fingerprint: row
        for row in session.scalars(
            select(ReconciliationFinding)
            .where(ReconciliationFinding.status == "explained")
            .execution_options(populate_existing=True)
        )
    }

    open_errors = 0
    for d in divergences:
        fingerprint = _fingerprint(d.kind, d.subject, d.detail)
        previous = explained.get(fingerprint)
        session.add(ReconciliationFinding(
            run_id=run.id,
            kind=d.kind,
            severity=d.severity.value,
            subject=d.subject[:4000],
            detail=d.detail[:4000],
            job_id=d.job_id,
            fingerprint=fingerprint,
            status="explained" if previous else "open",
            explanation=previous.explanation if previous else None,
            explained_by=previous.explained_by if previous else None,
            explained_at=previous.explained_at if previous else None,
        ))
        if previous:
            report.explained_count += 1
        else:
            report.open_count += 1
            if d.severity is Severity.ERROR:
                open_errors += 1
    report.open_errors = open_errors

    _audit(
        session, report, actor=actor, source=source, action="catalog.reconcile",
        target_type="host", target_id=result.host,
        payload={
            "run_id": report.run_id,
            "jobs_in_catalog": report.jobs_in_catalog,
            "jobs_in_crontab": report.jobs_in_crontab,
            "divergences": len(divergences),
            "open": report.open_count,
            "explained": report.explained_count,
            "open_errors": open_errors,
            "dry_run": dry_run,
        },
        occurred_at=now,
    )

    if dry_run:
        session.rollback()
        report.run_id = None
    else:
        session.flush()


@dataclass
class _AuditCounter:
    """`_audit` conta eventos no relatório que recebe; explain tem o seu."""

    audit_events: int = 0


def explain_finding(
    session: Session,
    fingerprint: str,
    *,
    explanation: str,
    actor: str,
    source: str = "cli",
) -> int:
    """Marca como explicada toda ocorrência de uma divergência.

    Explicar é ato de catálogo, então é auditado como qualquer escrita
    (invariante 3): alguém assume que aquela divergência é conhecida e aceita.
    """
    rows = list(session.scalars(
        select(ReconciliationFinding)
        .where(ReconciliationFinding.fingerprint == fingerprint)
        .execution_options(populate_existing=True)
    ))
    now = datetime.now(timezone.utc)
    for row in rows:
        row.status = "explained"
        row.explanation = explanation
        row.explained_by = actor
        row.explained_at = now

    if rows:
        _audit(
            session, _AuditCounter(), actor=actor, source=source,
            action="reconciliation.finding.explain",
            target_type="reconciliation_finding", target_id=fingerprint,
            payload={"explanation": explanation, "occurrences": len(rows),
                     "kind": rows[0].kind, "subject": rows[0].subject},
            occurred_at=now,
        )
    session.flush()
    return len(rows)
