"""CLI do job-catalog.

    catalog import <pacote> [--out DIR] [--vocabulary FILE]

Lê o pacote de coleta, monta os registros de job e escreve o relatório de
import. Não toca banco nenhum nesta etapa — o objetivo é validar o seed contra
os dados reais antes de persistir (Etapa 3 do plano).
"""

from __future__ import annotations

from pathlib import Path

import typer

from .build import Severity, build_catalog
from .naming import Vocabulary
from .report import (
    render_markdown,
    write_clients_mapping,
    write_clients_todo,
    write_findings_csv,
    write_jobs_csv,
)
from .ingest_processes import DEFAULT_FRAMEWORK_ROOT
from .seedpkg import SeedPackage

app = typer.Typer(add_completion=False, help="Job Catalog — Batch DTU")


@app.command("import")
def import_package(
    package: Path = typer.Argument(..., help="Diretório do pacote extraído (batch-seed-<host>-<stamp>)"),
    out: Path = typer.Option(Path("seed/raw/reports"), "--out", help="Onde escrever os relatórios"),
    vocabulary: Path | None = typer.Option(None, "--vocabulary", help="YAML de curadoria alternativo"),
) -> None:
    """Monta o catálogo a partir do pacote e escreve o relatório de import."""
    pkg = SeedPackage(package)
    vocab = Vocabulary.load(vocabulary)
    result = build_catalog(pkg, vocab)

    out.mkdir(parents=True, exist_ok=True)
    stem = f"{result.host}"
    report_path = out / f"import-{stem}.md"
    report_path.write_text(render_markdown(result), encoding="utf-8")
    write_jobs_csv(result, out / f"jobs-{stem}.csv")
    write_findings_csv(result, out / f"findings-{stem}.csv")
    # Por host, como os demais: com dois hosts em escopo (PROD e UAT), nome fixo
    # fazia o segundo import sobrescrever a curadoria do primeiro em silêncio.
    write_clients_todo(result, out / f"clients.todo-{stem}.yaml")
    write_clients_mapping(result, out / f"clients.derived-{stem}.yaml")

    errors = sum(1 for f in result.findings if f.severity is Severity.ERROR)
    warnings = sum(1 for f in result.findings if f.severity is Severity.WARNING)

    typer.echo(f"host ................: {result.host}")
    typer.echo(f"timezone ............: {result.timezone}")
    typer.echo(f"jobs ................: {len(result.jobs)} "
               f"({len(result.enabled_jobs)} ativos, {len(result.disabled_jobs)} desabilitados)")
    typer.echo(f"contratos ...........: {len(result.contracts)} "
               f"({len(result.orphan_contracts)} orfaos)")
    typer.echo(f"wrappers ............: {len(result.wrappers)}")
    typer.echo(f"achados .............: {errors} erros, {warnings} avisos, "
               f"{len(result.findings) - errors - warnings} infos")
    typer.echo(f"relatorio ...........: {report_path}")

    if errors:
        raise typer.Exit(code=1)


@app.command("load")
def load(
    package: Path = typer.Argument(..., help="Diretório do pacote extraído"),
    vocabulary: Path | None = typer.Option(None, "--vocabulary"),
    actor: str = typer.Option("cli", "--actor", help="Ator registrado na auditoria"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simula e reverte a transação"),
    role: str = typer.Option("app", "--role", help="Papel de conexão: app | migrations | test"),
) -> None:
    """Persiste o catálogo no banco. Idempotente: rodar de novo não duplica."""
    from .db.repository import load_catalog
    from .db.session import session_scope

    pkg = SeedPackage(package)
    vocab = Vocabulary.load(vocabulary)
    result = build_catalog(pkg, vocab)

    tarball = next(package.parent.glob(f"{package.name}.tar.gz"), None)
    digest = None
    if tarball is not None:
        from .seedpkg import sha256_file

        digest = sha256_file(tarball)

    with session_scope(role) as session:
        report = load_catalog(
            result, session, actor=actor, source="cli", dry_run=dry_run, bundle_sha256=digest
        )

    typer.echo(f"host ..................: {report.host}")
    if report.dry_run:
        typer.echo("MODO ..................: dry-run (nada persistido)")
    typer.echo(f"jobs ..................: +{report.jobs_created} criados, "
               f"~{report.jobs_updated} atualizados, ={report.jobs_unchanged} sem mudanca")
    typer.echo(f"agendas ...............: +{report.schedules_created} criadas, "
               f"~{report.schedules_updated} atualizadas")
    typer.echo(f"versoes de contrato ...: +{report.contract_versions_created}")
    typer.echo(f"aliases ...............: {report.aliases_upserted}")
    typer.echo(f"linhas de crontab .....: {report.cron_entries_stored}")
    typer.echo(f"findings ..............: {report.findings_stored} "
               f"({report.findings_carried} com explicacao herdada)")
    typer.echo(f"audit_event ...........: {report.audit_events}")
    for note in report.notes:
        typer.echo(f"nota ..................: {note}")


@app.command("reconcile")
def reconcile_command(
    package: Path = typer.Argument(..., help="Diretório do pacote de coleta FRESCO do host"),
    vocabulary: Path | None = typer.Option(None, "--vocabulary"),
    actor: str = typer.Option("system", "--actor", help="Ator registrado na auditoria"),
    role: str = typer.Option("app", "--role", help="Papel de conexão: app | migrations | test"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simula e reverte a transação"),
    out: Path | None = typer.Option(None, "--out", help="Grava o relatório de divergências em CSV"),
    fail_on_open: bool = typer.Option(
        True, "--fail-on-open/--no-fail-on-open",
        help="Sai com código 1 se houver divergência ABERTA de severidade erro",
    ),
) -> None:
    """Reconcilia crontab × catálogo. Não escreve catálogo — só o run de diff."""
    from .db.reconcile import reconcile
    from .db.session import session_scope

    pkg = SeedPackage(package)
    vocab = Vocabulary.load(vocabulary)
    result = build_catalog(pkg, vocab)

    with session_scope(role) as session:
        report = reconcile(result, session, actor=actor, source="cli", dry_run=dry_run)
        divergences = list(report.divergences)

    typer.echo(f"host ..................: {report.host}")
    if dry_run:
        typer.echo("MODO ..................: dry-run (nada persistido)")
    typer.echo(f"run ...................: {report.run_id or '-'}")
    typer.echo(f"jobs no catalogo ......: {report.jobs_in_catalog}")
    typer.echo(f"jobs na coleta ........: {report.jobs_in_crontab}")
    typer.echo(f"divergencias ..........: {len(divergences)} "
               f"({report.open_count} abertas, {report.explained_count} explicadas)")
    for kind, total in sorted(report.by_kind.items(), key=lambda kv: -kv[1]):
        typer.echo(f"  {kind:.<34} {total}")
    typer.echo(f"abertas com severidade erro: {report.open_errors}")

    if out is not None:
        import csv

        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["severidade", "tipo", "sujeito", "detalhe"])
            for d in divergences:
                writer.writerow([d.severity.value, d.kind, d.subject, d.detail])
        typer.echo(f"relatorio .............: {out}")

    if fail_on_open and report.open_errors:
        raise typer.Exit(code=1)


@app.command("explain")
def explain_command(
    fingerprint: str = typer.Argument(..., help="Fingerprint da divergência (sha256)"),
    reason: str = typer.Option(..., "--reason", help="Por que esta divergência é aceitável"),
    actor: str = typer.Option(..., "--actor", help="Quem assume a explicação"),
    role: str = typer.Option("app", "--role"),
) -> None:
    """Marca uma divergência como explicada. A explicação sobrevive aos runs."""
    from .db.reconcile import explain_finding
    from .db.session import session_scope

    with session_scope(role) as session:
        total = explain_finding(session, fingerprint, explanation=reason,
                                actor=actor, source="cli")

    if not total:
        typer.echo(f"nenhuma divergencia com fingerprint {fingerprint}")
        raise typer.Exit(code=1)
    typer.echo(f"explicadas ............: {total} ocorrencia(s)")


@app.command("validate")
def validate_command(
    target: Path = typer.Argument(..., help="Contrato JSON ou diretório de contratos"),
    strict: bool = typer.Option(
        False, "--strict",
        help="Política de escrita nova: qualquer erro faz o comando falhar",
    ),
) -> None:
    """Valida contrato(s) contra o schema — o mesmo validador da escrita."""
    import json

    from .contract_schema import validate_contract

    paths = sorted(target.rglob("*.json")) if target.is_dir() else [target]
    invalid = 0
    for path in paths:
        try:
            contract = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            invalid += 1
            typer.echo(f"{path.name}	json-invalido	{exc}")
            continue
        report = validate_contract(contract)
        if report.valid and not report.warnings:
            continue
        if not report.valid:
            invalid += 1
        for violation in report.violations:
            typer.echo(f"{path.name}	{violation.severity.value}	"
                       f"{violation.path}	{violation.rule}	{violation.message}")

    typer.echo(f"contratos .............: {len(paths)} ({invalid} invalidos)")
    if invalid and strict:
        raise typer.Exit(code=1)


@app.command("triage")
def triage_command(
    host: str | None = typer.Option(None, "--host", help="Host; omitido = todos no catálogo"),
    out: Path | None = typer.Option(None, "--out", help="Grava o relatório em markdown"),
    todos: bool = typer.Option(False, "--todos", help="Inclui achados já explicados"),
    role: str = typer.Option("app", "--role"),
) -> None:
    """Agrupa os achados abertos por quem decide, com o fingerprint de cada um."""
    from .db.queries import hosts_no_catalogo, triagem
    from .db.session import session_scope
    from .report import render_triagem

    partes: list[str] = []
    resumo: list[tuple[str, int, int]] = []
    with session_scope(role) as session:
        alvos = [host] if host else hosts_no_catalogo(session)
        if not alvos:
            typer.echo("catalogo vazio — rode `catalog load` antes")
            raise typer.Exit(code=1)
        for alvo in alvos:
            t = triagem(session, alvo, apenas_abertos=not todos)
            partes.append(render_triagem(t))
            resumo.append((alvo, len(t.achados),
                           sum(1 for a in t.achados if a.severity == "erro")))

    texto = "\n\n---\n\n".join(partes)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(texto, encoding="utf-8")
        typer.echo(f"relatorio .............: {out}")
    else:
        typer.echo(texto)

    for alvo, total, erros in resumo:
        typer.echo(f"{alvo:.<38} {total} aberto(s), {erros} erro(s)")


@app.command("sample")
def sample_command(
    host: str = typer.Argument(..., help="Host a amostrar"),
    por_dominio: int = typer.Option(3, "--por-dominio", help="Jobs por domínio"),
    seed: int = typer.Option(20260911, "--seed", help="Semente — mesma semente, mesma amostra"),
    out: Path | None = typer.Option(None, "--out"),
    role: str = typer.Option("app", "--role"),
) -> None:
    """Amostra determinística por domínio para a conferência humana."""
    from .db.queries import amostra_por_dominio
    from .db.session import session_scope
    from .report import render_amostra

    with session_scope(role) as session:
        amostra = amostra_por_dominio(session, host, por_dominio=por_dominio, seed=seed)

    if not amostra:
        typer.echo(f"nenhum job do host {host} no catalogo")
        raise typer.Exit(code=1)

    texto = render_amostra(host, amostra, seed=seed)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(texto, encoding="utf-8")
        typer.echo(f"checklist .............: {out}")
    else:
        typer.echo(texto)
    typer.echo(f"jobs ..................: {sum(len(v) for v in amostra.values())} "
               f"em {len(amostra)} dominios (semente {seed})")


@app.command("history")
def history_command(
    process_name: str = typer.Argument(..., help="Nome do processo (ex.: prd_aaa_col_rpt)"),
    host: str | None = typer.Option(None, "--host", help="Desambigua quando o job existe nos dois"),
    role: str = typer.Option("app", "--role"),
) -> None:
    """Histórico de versões de contrato e revisões de metadados, com diffs."""
    from .db.queries import historico, job_por_nome
    from .db.session import session_scope
    from .report import render_historico

    with session_scope(role) as session:
        jobs = job_por_nome(session, process_name, host)
        if not jobs:
            typer.echo(f"job nao encontrado: {process_name}")
            raise typer.Exit(code=1)
        if len(jobs) > 1 and host is None:
            typer.echo(f"`{process_name}` existe em {len(jobs)} hosts "
                       f"({', '.join(j.host for j in jobs)}); use --host")
            raise typer.Exit(code=1)
        texto = render_historico(jobs[0], historico(session, jobs[0]))
    typer.echo(texto)


@app.command("check-names")
def check_names(
    package: Path = typer.Argument(..., help="Diretório do pacote extraído"),
    vocabulary: Path | None = typer.Option(None, "--vocabulary"),
) -> None:
    """Mostra a decomposição do nome de cada contrato — útil ao curar o vocabulário."""
    from .naming import parse_process_name

    pkg = SeedPackage(package)
    vocab = Vocabulary.load(vocabulary)
    for path in pkg.contracts():
        domain = path.parent.name
        parsed = parse_process_name(path.name, vocab, domain_dir=domain)
        typer.echo(
            "\t".join([
                path.name,
                parsed.environment or "-",
                parsed.client_code or "-",
                ",".join(parsed.country_codes) or "-",
                parsed.domain_from_name or "-",
                parsed.process_suffix or "-",
                ",".join(parsed.flags) or "-",
            ])
        )


@app.command("ingest-processes")
def ingest_processes(
    directory: Path = typer.Argument(
        ...,
        help="Diretório de contratos, ex.: /opt2/batch_v2/batch-commons-framework/processes/base2",
    ),
    host: str = typer.Option(..., "--host", help="Hostname do catálogo (ex.: srv-sftp-2)"),
    environment: str = typer.Option(..., "--environment", help="PROD | UAT | TEST | DEV"),
    domain: str | None = typer.Option(
        None, "--domain", help="Default: o nome do diretório (base2, reportes, ...)"
    ),
    framework_root: str = typer.Option(
        DEFAULT_FRAMEWORK_ROOT, "--framework-root",
        help="Raiz do framework NO HOST — compõe o contract_path gravado",
    ),
    status: str = typer.Option(
        "on_demand", "--status",
        help="Status inicial. Sem crontab não há agenda: 'on_demand' é o honesto",
    ),
    actor: str = typer.Option("cli", "--actor", help="Ator registrado na auditoria"),
    role: str = typer.Option("app", "--role", help="Papel de conexão: app | migrations | test"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simula e reverte a transação"),
) -> None:
    """Carrega no RDS os contratos de um diretório (um `.json` = um job).

    Diferente de `catalog load`, que parte do pacote do coletor e traz agenda e
    crontab junto. Use este quando a fonte for a árvore de contratos.
    """
    from .db.session import session_scope
    from .ingest_processes import ingest_directory

    if environment.upper() not in ("PROD", "UAT", "TEST", "DEV"):
        typer.echo(f"environment invalido: {environment}", err=True)
        raise typer.Exit(code=2)

    with session_scope(role) as session:
        report = ingest_directory(
            directory, session, host=host, environment=environment.upper(),
            domain=domain, actor=actor, framework_root=framework_root,
            status=status, dry_run=dry_run,
        )

    typer.echo(f"host ..................: {report.host} ({report.environment})")
    typer.echo(f"dominio ...............: {report.domain}")
    typer.echo(f"contract_path .........: {report.contract_root}/<arquivo>.json")
    if report.dry_run:
        typer.echo("MODO ..................: dry-run (nada persistido)")
    typer.echo(f"arquivos lidos ........: {report.files_read}")
    typer.echo(f"jobs ..................: +{report.jobs_created} criados, "
               f"~{report.jobs_updated} atualizados, ={report.jobs_unchanged} sem mudanca")
    typer.echo(f"versoes de contrato ...: +{report.versions_created} novas, "
               f"={report.versions_reused} ja existentes")
    typer.echo(f"audit_event ...........: {report.audit_events}")
    if report.schema_invalid:
        typer.echo(f"contratos invalidos ...: {len(report.schema_invalid)} "
                   f"(carregados mesmo assim, veredito gravado na versao)")
        for linha in report.schema_invalid[:10]:
            typer.echo(f"  - {linha}")
    if report.missing_in_directory:
        typer.echo(f"no catalogo e fora do diretorio: {len(report.missing_in_directory)} "
                   f"(NAO removidos — use `catalog reconcile`)")
        for caminho in report.missing_in_directory[:10]:
            typer.echo(f"  - {caminho}")
    if report.invalid_json:
        typer.echo(
            f"JSON quebrado .........: {len(report.invalid_json)} (NAO carregados)", err=True
        )
        for linha in report.invalid_json:
            typer.echo(f"  - {linha}", err=True)
        raise typer.Exit(code=1)


if __name__ == "__main__":  # pragma: no cover
    app()
