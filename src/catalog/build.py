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
from .contract_schema import ValidationReport, validate_contract
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
    # Destino downstream de `upload_remote`/`download_remote`, resolvido pelo
    # jump SFTP — não é alias local. Para a Fase 3 é o "a quem este job entrega".
    remote_targets: tuple[str, ...] = ()
    step_functions: tuple[str, ...] = ()
    # Declarado DENTRO do contrato — fonte autoritativa, superior ao nome
    contract_client: str | None = None
    contract_country: str | None = None
    contract_environment: str | None = None
    contract_process: str | None = None
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
    alias_metadata: list[dict]
    host_info: dict[str, str]

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

    _detect_client_outliers(jobs, findings, vocab)
    _detect_undeclared_aliases(jobs, package, findings)

    aliases, alias_metadata = _aliases_from_package(package)

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
        alias_metadata=alias_metadata,
        host_info=package.host_info,
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

    contract_hash = contract_bytes = None
    facts = ContractFacts()
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
            facts = _inspect_contract(local)
            if facts.error:
                findings.append(
                    Finding("contrato-json-invalido", Severity.ERROR, contract_ref, facts.error)
                )
                flags.append("json-invalido")
            if facts.validation is not None:
                # Contrato inválido NÃO bloqueia o import (Policy.LEGACY): o
                # catálogo registra o parque como ele é. Vira finding para que
                # a reconciliação cobre a correção.
                for violation in facts.validation.errors:
                    findings.append(
                        Finding("contrato-invalido", Severity.ERROR, contract_ref,
                                f"{violation.path}: {violation.message}")
                    )
                for violation in facts.validation.warnings:
                    findings.append(
                        Finding("contrato-com-aviso", Severity.WARNING, contract_ref,
                                f"{violation.path}: {violation.message}")
                    )
                if facts.validation.errors:
                    flags.append("schema-invalido")
            if facts.schema_version is None and not facts.error:
                flags.append("sem-schema-version")
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

    # O contrato é a fonte autoritativa das dimensões: ele DECLARA client,
    # country e environment. A convenção de nome é fallback para job sem
    # contrato resolvido. Divergência entre as duas fontes é finding — é a
    # mesma classe de risco do alias cujo nome cita um cliente e cuja
    # credencial é de outro.
    environment = _normalize_environment(facts.environment, vocab) or parsed.environment
    client_name = facts.client or parsed.client_name
    countries = _normalize_countries(facts.country, vocab) or parsed.country_codes

    if facts.environment and parsed.environment and environment != parsed.environment:
        findings.append(
            Finding("dimensao-divergente", Severity.WARNING, process_name,
                    f"environment: nome diz {parsed.environment}, contrato diz {facts.environment}")
        )
    # Comparação por CONJUNTO: `cri_slv_gtm` no nome e
    # `costa_rica_guatemala_salvador` no contrato são o mesmo escopo em ordem
    # diferente. Nome que é subconjunto do contrato é abreviação, não conflito
    # (`prd_stb_per_...` com contrato `peru_colombia_venezuela`).
    if facts.country and parsed.country_codes:
        do_nome, do_contrato = set(parsed.country_codes), set(countries)
        if do_nome and do_contrato and not do_nome.issubset(do_contrato):
            findings.append(
                Finding("dimensao-divergente", Severity.WARNING, process_name,
                        f"country: nome diz {','.join(sorted(do_nome))}, "
                        f"contrato diz {facts.country}")
            )

    return JobRecord(
        process_name=process_name,
        domain=domain,
        environment=environment,
        client_code=parsed.client_code,
        client_name=client_name,
        country_codes=countries,
        schedule=entry.schedule,
        timezone=timezone,
        enabled=entry.enabled,
        status_reason=entry.status_reason or entry.inline_comment,
        wrapper_path=wrapper_path,
        contract_path=contract_ref,
        contract_hash=contract_hash,
        contract_bytes=contract_bytes,
        schema_version=facts.schema_version,
        steps_count=facts.steps_count,
        manual_steps=invocation.manual_steps if invocation else None,
        dates_pattern=invocation.dates_pattern if invocation else None,
        no_mail=bool(invocation.no_mail) if invocation else False,
        log_path=entry.log_path,
        cron_source=entry.source,
        cron_lineno=entry.lineno,
        connection_aliases=facts.aliases,
        remote_targets=facts.remote_targets,
        step_functions=facts.functions,
        contract_client=facts.client,
        contract_country=facts.country,
        contract_environment=facts.environment,
        contract_process=facts.process,
        flags=tuple(flags),
    )


# Schema real dos contratos, confirmado nos 574 arquivos de 09/2026:
#   topo : name_process, client, country, environment, description,
#          send_infra_mail, steps[], additional_info
#   step : step, function, stop_on_failed + campos por função
#          (files, server, server_remote, command, key, arguments, mails...)
#
# `schema_version` NÃO existe em nenhum contrato: o campo previsto no
# invariante 1 é uma extensão futura, não o estado atual. Contrato sem ele é
# registrado como versão de schema nula (legado), nunca como erro.
#
# O alias de conexão vive em `server` — e SÓ nele. Verificado no `main.sh`
# (09/2026): em `upload_remote`/`download_remote` a conexão é sempre
# `$SFTP_SERVER_CUSTOMER_UPLOAD` (o alias `sftp_customer_upload`, o jump SFTP),
# e o `server_remote` é passado como PARÂMETRO para `send_remote_command.sh`
# nesse jump host:
#
#     server=$(jq --arg server "${SFTP_SERVER_CUSTOMER_UPLOAD}" \
#              '.[] | select(.name==$server)' <<<"${serverVar}")
#     ... send_remote_command.sh upload '<dir>@${serverName}@...'
#
# Ou seja: `server_remote` nomeia o DESTINO downstream conhecido pelo jump host,
# não uma entrada do connections.json local. Tratá-lo como alias produzia 84
# falsos `alias-nao-declarado` — 71 deles em jobs ATIVOS de produção que sempre
# funcionaram. `local` é pseudo-alias de operação local e não é conexão.
STEP_ALIAS_KEYS = ("server",)
STEP_REMOTE_TARGET_KEYS = ("server_remote",)
# Funções cuja conexão real é o jump SFTP, resolvido em runtime pela env var.
REMOTE_JUMP_FUNCTIONS = frozenset({"upload_remote", "download_remote"})
PSEUDO_ALIASES = frozenset({"local", "localhost", ""})


@dataclass(frozen=True)
class ContractFacts:
    schema_version: str | None = None
    steps_count: int | None = None
    aliases: tuple[str, ...] = ()
    # Destinos downstream de `server_remote`: nomes conhecidos pelo jump host,
    # não aliases locais. São o cliente final de um `upload_remote`.
    remote_targets: tuple[str, ...] = ()
    functions: tuple[str, ...] = ()
    client: str | None = None
    country: str | None = None
    environment: str | None = None
    process: str | None = None
    error: str | None = None
    # Veredito do schema (contract_schema.py). None = contrato não lido.
    validation: ValidationReport | None = None


def _inspect_contract(path: Path) -> ContractFacts:
    """Extrai do contrato o que o catálogo precisa, no schema real."""
    import json

    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        return ContractFacts(error=f"{exc.msg} (linha {exc.lineno}, coluna {exc.colno})")

    if not isinstance(data, dict):
        return ContractFacts(error="raiz do contrato nao e objeto JSON")

    raw_steps = data.get("steps")
    steps = raw_steps if isinstance(raw_steps, list) else []

    aliases: list[str] = []
    remote_targets: list[str] = []
    functions: list[str] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        function = step.get("function")
        if isinstance(function, str) and function:
            functions.append(function)
        for key, destino in ((STEP_ALIAS_KEYS, aliases),
                             (STEP_REMOTE_TARGET_KEYS, remote_targets)):
            for k in key:
                value = step.get(k)
                if isinstance(value, str) and value.strip().lower() not in PSEUDO_ALIASES:
                    destino.append(value.strip())

    return ContractFacts(
        validation=validate_contract(data),
        schema_version=str(data["schema_version"]) if data.get("schema_version") else None,
        steps_count=len(steps),
        aliases=tuple(dict.fromkeys(aliases)),
        remote_targets=tuple(dict.fromkeys(remote_targets)),
        functions=tuple(dict.fromkeys(functions)),
        client=(data.get("client") or None),
        country=(data.get("country") or None),
        environment=(data.get("environment") or None),
        process=(data.get("name_process") or None),
    )


def _detect_client_outliers(
    jobs: list[JobRecord], findings: list[Finding], vocab: Vocabulary | None = None
) -> None:
    """Código de cliente que quase sempre é um nome e raramente é outro.

    O caso real: 110 jobs `stb_*` declaram `servitebca` e UM declara
    `codesarrollo`. Não é variação ortográfica — é cliente errado no contrato,
    a mesma classe de risco do alias cujo nome cita um cliente e cuja
    credencial é de outro. Num step `upload_remote` significa mandar arquivo
    para o cliente errado.
    """
    from collections import Counter, defaultdict

    por_codigo: dict[str, Counter] = defaultdict(Counter)
    exemplos: dict[tuple[str, str], str] = {}
    for job in jobs:
        if job.client_code and job.contract_client:
            por_codigo[job.client_code][job.contract_client] += 1
            exemplos.setdefault((job.client_code, job.contract_client), job.process_name)

    for code, nomes in por_codigo.items():
        if len(nomes) < 2:
            continue
        (dominante, quantos), *resto = nomes.most_common()
        for nome, poucos in resto:
            if _mesma_familia(nome, dominante):
                continue  # variação ortográfica do mesmo cliente
            declarados = (vocab.shared_client_codes.get(code, set()) if vocab else set())
            if nome.lower() in declarados and dominante.lower() in declarados:
                continue  # código compartilhado declarado no vocabulário
            if poucos * 10 <= quantos:
                findings.append(
                    Finding(
                        "cliente-outlier-no-contrato", Severity.ERROR,
                        exemplos[(code, nome)],
                        f"codigo '{code}' declara '{nome}' em {poucos} job(s) mas "
                        f"'{dominante}' em {quantos}; conferir o campo client do contrato",
                    )
                )
            else:
                findings.append(
                    Finding(
                        "codigo-de-cliente-ambiguo", Severity.WARNING, code,
                        f"'{dominante}' ({quantos} jobs) e '{nome}' ({poucos} jobs) "
                        "compartilham o mesmo codigo",
                    )
                )


def _mesma_familia(a: str, b: str) -> bool:
    """Variação ortográfica: caixa, separador ou um contendo o outro."""
    x = a.lower().replace("_", "").replace("-", "")
    y = b.lower().replace("_", "").replace("-", "")
    return x == y or x in y or y in x


def _detect_undeclared_aliases(
    jobs: list[JobRecord], package: SeedPackage, findings: list[Finding]
) -> None:
    """Alias citado no contrato e ausente do connections.json.

    O job falha na resolução de credencial em runtime — e hoje isso só aparece
    quando o step quebra.
    """
    declarados, _ = _aliases_from_package(package)
    if not declarados:
        return
    from collections import Counter

    citados = Counter(a for job in jobs for a in job.connection_aliases)
    for alias, usos in citados.items():
        if alias not in declarados:
            findings.append(
                Finding("alias-nao-declarado", Severity.ERROR, alias,
                        f"citado por {usos} job(s) e ausente do connections.json")
            )


def _normalize_environment(raw: str | None, vocab: Vocabulary) -> str | None:
    """`environment` do contrato vem na mesma grafia do prefixo do nome (prd/uat...)."""
    if not raw:
        return None
    return vocab.environments.get(raw.strip().lower())


def _normalize_countries(raw: str | None, vocab: Vocabulary) -> tuple[str, ...]:
    """`country` do contrato vem por extenso (`ecuador`, `republica_dominicana`)."""
    if not raw:
        return ()
    token = raw.strip().lower().replace(" ", "_")
    direct = vocab.countries.get(token)
    if direct:
        return (direct,)

    # Nome composto (`costa_rica_guatemala_salvador`): casar pela MAIOR
    # sequência de tokens primeiro, senão `costa_rica` se perde — nem `costa`
    # nem `rica` existem isolados no vocabulário.
    parts = [p for p in token.split("_") if p]
    found: list[str] = []
    index = 0
    while index < len(parts):
        for size in range(min(3, len(parts) - index), 0, -1):
            candidate = "_".join(parts[index : index + size])
            code = vocab.countries.get(candidate)
            if code:
                if code not in found:
                    found.append(code)
                index += size
                break
        else:
            index += 1
    return tuple(found)


def _domain_from_path(path: str | None, anchor: str) -> str | None:
    if not path:
        return None
    marker = f"/{anchor}/"
    if marker not in path:
        return None
    tail = path.split(marker, 1)[1]
    parts = tail.split("/")
    return parts[0] if len(parts) > 1 else None


ALIAS_COLUMNS = ("name", "type", "host", "username", "port", "region", "auth_method", "key_path")


def _aliases_from_package(package: SeedPackage) -> tuple[set[str], list[dict]]:
    """Aliases declarados no connections.json, como nomes e como metadado.

    O coletor emite `name type host user port region auth key_path campos`;
    nenhum valor de senha sai de lá, só o método de autenticação.
    """
    section = package.first("CONNECTION-ALIASES")
    names: set[str] = set()
    rows: list[dict] = []
    for row in section.tsv():
        if not row or not row[0] or row[0].startswith("("):
            continue
        names.add(row[0])
        record = {key: (row[index] if index < len(row) else "") or None
                  for index, key in enumerate(ALIAS_COLUMNS)}
        rows.append(record)
    return names, rows
