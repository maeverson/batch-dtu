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
from .report import render_markdown, write_clients_todo, write_findings_csv, write_jobs_csv
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
    write_clients_todo(result, out / "clients.todo.yaml")

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


if __name__ == "__main__":  # pragma: no cover
    app()
