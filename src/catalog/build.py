"""Construção do catálogo a partir do pacote de coleta.

Cruza as três fontes do legado para produzir um registro por job:

    linha de crontab  ->  wrapper (schedulers/<dominio>/x.sh)  ->  contrato (processes/<dominio>/y.json)
      agenda, status,       parametros de invocacao                 steps, schema_version
      status_reason         (steps, data-alvo, no-mail)

Toda divergência gera `Finding` — nada é corrigido nem descartado em silêncio,
porque é o relatório de reconciliação que sustenta o critério de aceite da fase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .cron import CronEntry, EntryKind, parse_crontab
from .naming import ProcessName, Vocabulary, parse_process_name
from .seedpkg import SeedPackage, sha256_file
from .wrapper import Invocation, WrapperScript, parse_wrapper


class Severity(str, Enum):
    ERROR = "erro"       # job quebrado hoje, em produção
    WARNING = "aviso"    # inconsistência que exige decisão humana
    INFO = "info"        # observação para curadoria


@dataclass
class Finding:
    kind: str
    severity: Severity
    subject: str
    detail: str

    def as_row(self) -> list[str]:
        return [self.severity.value, self.kind, self.subject, self.detail]


@dataclass
class JobRecord:
    """Candidato a linha da tabela `job` (ver docs/modelo-de-dados.md)."""

    process_name: str
    domain: str | None
    environment: str | None
    client_code: str | None
    client_name: str | None
    country_codes: tuple[str, ...]
    schedule: str | None
    timezone: str | None
    enabled: bool
    status_reason: str | None
    wrapper_path: str | None
    contract_path: str | None
    contract_hash: str | None
    contract_bytes: int | None
    schema_version: str | None
    steps_count: int | None
    manual_steps: str | None
    dates_pattern: str | None
    no_mail: bool
    log_path: str | None
    cron_source: str | None
    cron_lineno: int | None
    connection_aliases: tuple[str, ...] = ()
    flags: tuple[str, ...] = field(default_factory=tuple)


@dataclass
class BuildResult:
    host: str
    timezone: str | None
    framework_root: str
    jobs: list[JobRecord]
    findings: list[Finding]
    cron_entries: list[CronEntry]
    wrappers: dict[str, WrapperScript]
    contracts: dict[str, Path]
    orphan_contracts: list[str]
    aliases_declared: set[str]

    @property
    def enabled_jobs(self) -> list[JobRecord]:
        return [j for j in self.jobs if j.enabled]

    @property
    def disabled_jobs(self) -> list[JobRecord]:
        return [j for j in self.jobs if not j.enabled]


def build_catalog(package: SeedPackage, vocab: Vocabulary) -> BuildResult:
    findings: list[Finding] = []

    # 1. wrappers em disco
    wrappers: dict[str, WrapperScript] = {}
    for local in package.wrappers():
        server = package.server_path(local)
        script = parse_wrapper(server, local.read_text(encoding="utf-8", errors="replace"))
        wrappers[server] = script
        for flag in script.flags:
            findings.append(
                Finding("wrapper-" + flag.split(":")[0], Severity.WARNING, server, flag)
            )
        for invocation in script.invocations:
            for unresolved in invocation.unresolved:
                findings.append(
                    Finding(
                        "ref-nao-resolvida", Severity.WARNING, server,
                        f"linha {invocation.lineno}: {unresolved}",
                    )
                )

    # 2. contratos em disco
    contracts: dict[str, Path] = {package.server_path(p): p for p in package.contracts()}

    # 3. entradas de crontab
    cron_entries: list[CronEntry] = []
    for source, text in package.crontabs():
        cron_entries.extend(parse_crontab(text, source=source))

    for entry in cron_entries:
        if entry.kind is EntryKind.UNPARSED:
            findings.append(
                Finding("cron-nao-parseada", Severity.WARNING,
                        f"{entry.source}:{entry.lineno}", entry.raw.strip()[:160])
            )

    # 4. um JobRecord por entrada de cron que aponta para wrapper do framework
    jobs: list[JobRecord] = []
    referenced_contracts: set[str] = set()
    timezone = package.timezone

    for entry in cron_entries:
        if entry.kind is not EntryKind.JOB or not entry.script_path:
            continue

        if "/logs/" in entry.script_path:
            findings.append(
                Finding("script-em-diretorio-de-log",
                        Severity.ERROR if entry.enabled else Severity.WARNING,
                        f"{entry.source}:{entry.lineno}",
                        f"comando aponta para dentro de logs/: {entry.script_path}")
            )
        if entry.log_path and entry.log_path.endswith(".sh"):
            findings.append(
                Finding("log-com-extensao-sh", Severity.WARNING,
                        f"{entry.source}:{entry.lineno}",
                        f"redirecionamento grava log em arquivo .sh: {entry.log_path}")
            )

        script = wrappers.get(entry.script_path)
        if script is None:
            findings.append(
                Finding("wrapper-ausente",
                        Severity.ERROR if entry.enabled else Severity.WARNING,
                        f"{entry.source}:{entry.lineno}",
                        f"cron aponta para {entry.script_path}, ausente no pacote"
                        + (" (job ATIVO)" if entry.enabled else " (job desabilitado)"))
            )

        domain = _domain_from_path(entry.script_path, "schedulers")
        invocations = list(script.invocations) if script else []

        if not invocations:
            jobs.append(
                _job_from_parts(entry, None, None, domain, timezone, vocab, contracts,
                                referenced_contracts, findings)
            )
            continue

        for invocation in invocations:
            jobs.append(
                _job_from_parts(entry, invocation, invocation.process_file, domain,
                                timezone, vocab, contracts, referenced_contracts, findings)
            )

    # 5. contratos em disco que nenhum job referencia
    orphans = sorted(set(contracts) - referenced_contracts)
    for orphan in orphans:
        findings.append(
            Finding("contrato-orfao", Severity.INFO, orphan,
                    "existe em disco e nenhum job agendado o referencia")
        )

    aliases = _aliases_from_package(package)

    return BuildResult(
        host=package.host,
        timezone=timezone,
        framework_root=package.framework_root,
        jobs=jobs,
        findings=findings,
        cron_entries=cron_entries,
        wrappers=wrappers,
        contracts=contracts,
        orphan_contracts=orphans,
        aliases_declared=aliases,
    )


def _job_from_parts(
    entry: CronEntry,
    invocation: Invocation | None,
    contract_ref: str | None,
    domain: str | None,
    timezone: str | None,
    vocab: Vocabulary,
    contracts: dict[str, Path],
    referenced: set[str],
    findings: list[Finding],
) -> JobRecord:
    wrapper_path = entry.script_path
    process_name = Path(wrapper_path).stem if wrapper_path else "desconhecido"
    parsed: ProcessName = parse_process_name(process_name, vocab, domain_dir=domain)

    contract_hash = contract_bytes = schema_version = steps_count = None
    aliases: tuple[str, ...] = ()
    flags = list(parsed.flags)

    if contract_ref:
        referenced.add(contract_ref)
        local = contracts.get(contract_ref)
        if local is None:
            findings.append(
                Finding("contrato-ausente",
                        Severity.ERROR if entry.enabled else Severity.WARNING,
                        f"{entry.source}:{entry.lineno}",
                        f"{process_name} -> {contract_ref} nao existe"
                        + (" (job ATIVO)" if entry.enabled else " (job desabilitado)"))
            )
            flags.append("contrato-ausente")
        else:
            contract_hash = sha256_file(local)
            contract_bytes = local.stat().st_size
            schema_version, steps_count, aliases, parse_error = _inspect_contract(local)
            if parse_error:
                findings.append(
                    Finding("contrato-json-invalido", Severity.ERROR, contract_ref, parse_error)
                )
                flags.append("json-invalido")
            contract_domain = _domain_from_path(contract_ref, "processes")
            if contract_domain and domain and contract_domain != domain:
                findings.append(
                    Finding("dominio-cruzado", Severity.WARNING, process_name,
                            f"wrapper em schedulers/{domain}, contrato em processes/{contract_domain}")
                )
    elif invocation is not None:
        flags.append("sem-process-file")
        findings.append(
            Finding("invocacao-sem-contrato", Severity.WARNING, wrapper_path or process_name,
                    f"linha {invocation.lineno}: main.sh sem --process-file resolvido")
        )

    return JobRecord(
        process_name=process_name,
        domain=domain,
        environment=parsed.environment,
        client_code=parsed.client_code,
        client_name=parsed.client_name,
        country_codes=parsed.country_codes,
        schedule=entry.schedule,
        timezone=timezone,
        enabled=entry.enabled,
        status_reason=entry.status_reason or entry.inline_comment,
        wrapper_path=wrapper_path,
        contract_path=contract_ref,
        contract_hash=contract_hash,
        contract_bytes=contract_bytes,
        schema_version=schema_version,
        steps_count=steps_count,
        manual_steps=invocation.manual_steps if invocation else None,
        dates_pattern=invocation.dates_pattern if invocation else None,
        no_mail=bool(invocation.no_mail) if invocation else False,
        log_path=entry.log_path,
        cron_source=entry.source,
        cron_lineno=entry.lineno,
        connection_aliases=aliases,
        flags=tuple(flags),
    )


def _inspect_contract(path: Path) -> tuple[str | None, int | None, tuple[str, ...], str | None]:
    """Lê schema_version, número de steps e aliases citados no contrato."""
    import json

    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        return None, None, (), f"{exc.msg} (linha {exc.lineno}, coluna {exc.colno})"

    schema_version = None
    steps: list = []
    if isinstance(data, dict):
        schema_version = data.get("schema_version") or data.get("schemaVersion")
        raw_steps = data.get("steps") or data.get("Steps") or []
        if isinstance(raw_steps, list):
            steps = raw_steps
    elif isinstance(data, list):
        steps = data

    aliases: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        for key in ("connection", "connection_name", "conexion", "alias"):
            value = step.get(key)
            if isinstance(value, str) and value:
                aliases.append(value)

    version = str(schema_version) if schema_version is not None else None
    return version, len(steps), tuple(dict.fromkeys(aliases)), None


def _domain_from_path(path: str | None, anchor: str) -> str | None:
    if not path:
        return None
    marker = f"/{anchor}/"
    if marker not in path:
        return None
    tail = path.split(marker, 1)[1]
    parts = tail.split("/")
    return parts[0] if len(parts) > 1 else None


def _aliases_from_package(package: SeedPackage) -> set[str]:
    section = package.first("CONNECTION-ALIASES")
    return {row[0] for row in section.tsv() if row and row[0] and not row[0].startswith("(")}
