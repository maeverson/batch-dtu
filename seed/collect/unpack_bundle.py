#!/usr/bin/env python3
"""Desempacota o arquivo consolidado gerado por collect-bundle.sh.

Uso:
    python3 seed/collect/unpack_bundle.py <bundle.txt|bundle.txt.gz> [--out seed/raw]

Reconstrói o layout que o importador do catálogo consome:

    seed/raw/<host>/
        host-info.tsv, cron-summary.tsv, domain-census.tsv, ...
        crontabs/user-<user>.txt          (uma por fonte de agendamento)
        processes/<caminho absoluto>.json (contratos, com sha256 verificado)
        engine/<caminho absoluto>.sh      (fonte do framework, se coletado)
        unpack-report.txt

Verifica o sha256 de cada contrato/arquivo de engine contra o valor registrado na
coleta. Divergência é erro reportado, nunca silenciada.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import re
import sys
from pathlib import Path

MARKER = re.compile(r"^##### (BEGIN|END) (?P<name>[A-Z0-9-]+)(?P<attrs>.*)$")

# Seções de texto simples: nome da seção -> arquivo de destino
PLAIN_SECTIONS = {
    "HOST-INFO": "host-info.tsv",
    "OS-RELEASE": "os-release.txt",
    "TIMEDATECTL": "timedatectl.txt",
    "CRON-PERIODIC-LISTING": "cron-periodic-listing.txt",
    "SYSTEMD-TIMERS": "systemd-timers.txt",
    "CRON-ALLOW-DENY": "cron-allow-deny.txt",
    "CRON-TZ-DECLARATIONS": "cron-timezone-declarations.txt",
    "CRON-SUMMARY": "cron-summary.tsv",
    "DETECTED-PATHS": "detected-paths.tsv",
    "DOMAIN-CENSUS": "domain-census.tsv",
    "REFERENCED-DIRS": "referenced-dirs.tsv",
    "CONTRACTS-REFERENCED": "contracts-referenced.tsv",
    "CONTRACTS-INDEX": "contracts-listing.tsv",
    "SCHEDULERS-INDEX": "schedulers-listing.tsv",
    "CRON-REFERENCED-SCRIPTS": "cron-referenced-scripts.tsv",
    "PROCESS-FILE-REFS": "process-file-refs.tsv",
    "DOMAIN-DISTINCT-SCRIPTS": "domain-distinct-scripts.tsv",
    "CLIENT-CENSUS": "client-census.tsv",
    "CLIENT-DIRS": "client-dirs.txt",
    "WRAPPER-INVOCATION": "wrapper-invocation.tsv",
    "UNRESOLVED-REFS": "unresolved-refs.txt",
    "PATHOLOGY-NAMES": "pathology-names.txt",
    "CASE-COLLISIONS": "case-collisions.txt",
    "ORPHAN-CONTRACTS": "orphan-contracts.txt",
    "BROKEN-REFS": "broken-refs.txt",
    "ALIASES-USED-IN-CONTRACTS": "aliases-used-in-contracts.tsv",
    "FRAMEWORK-LISTING": "framework-listing.tsv",
    "PROCESSES-DIRS": "processes-dirs.txt",
    "CONNECTION-ALIASES": "connection-aliases.tsv",
    "CONNECTIONS-STAT": "connections-stat.txt",
    "BATCH-HOME-LISTING": "batch-home-listing.tsv",
    "FRAMEWORK-SHA256": "framework-sha256.txt",
    "CUSTOM-SCRIPTS-LISTING": "custom-scripts-listing.tsv",
    "LOGS-SAMPLE": "logs-sample.tsv",
    "SECRET-SCAN": "secret-scan-report.txt",
    "BUNDLE-END": "bundle-end.tsv",
}


def parse_attrs(raw: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for part in raw.split("|"):
        part = part.strip()
        if "=" in part:
            key, _, value = part.partition("=")
            attrs[key.strip()] = value.strip()
    return attrs


def read_lines(path: Path) -> list[str]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as handle:  # type: ignore[operator]
        return handle.read().splitlines()


def resolve_body(lines: list[str], expected_sha: str | None) -> tuple[str, str]:
    """Reconstrói o conteúdo e casa com o sha256 registrado na coleta.

    collect-bundle.sh acrescenta um '\\n' antes do marcador END para garantir que ele
    comece em início de linha; isso pode gerar uma linha vazia extra quando o arquivo
    original já terminava em newline. Testamos as variações e escolhemos a que casa.
    """
    joined = "\n".join(lines)
    candidates = [joined + "\n", joined, joined.rstrip("\n")]
    if expected_sha:
        for candidate in candidates:
            digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()
            if digest == expected_sha:
                return candidate, "sha256-ok"
        return candidates[0], "SHA256-DIVERGENTE"
    return candidates[0], "sem-sha256"


def target_for_abs_path(base: Path, abs_path: str) -> Path:
    return base / abs_path.lstrip("/")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--out", type=Path, default=Path("seed/raw"))
    args = parser.parse_args()

    if not args.bundle.is_file():
        print(f"arquivo não encontrado: {args.bundle}", file=sys.stderr)
        return 1

    lines = read_lines(args.bundle)

    # 1ª passada: descobrir o host (HOST-INFO)
    host = None
    inside_host_info = False
    for line in lines:
        marker = MARKER.match(line)
        if marker:
            inside_host_info = marker.group(1) == "BEGIN" and marker.group("name") == "HOST-INFO"
            continue
        if inside_host_info and line.startswith("hostname_short\t"):
            host = line.split("\t", 1)[1].strip()
            break
    if not host:
        host = args.bundle.name.split("-")[2] if args.bundle.name.count("-") >= 2 else "unknown-host"
        print(f"aviso: HOST-INFO ausente, usando host={host}", file=sys.stderr)

    out = args.out / host
    out.mkdir(parents=True, exist_ok=True)
    (out / "crontabs").mkdir(exist_ok=True)

    report: list[str] = []
    # Arquivos que acumulam múltiplas seções: a 1ª escrita deste run trunca,
    # as seguintes acrescentam (evita duplicar ao reprocessar o mesmo bundle).
    touched: set[Path] = set()
    counters = {"contracts": 0, "wrappers": 0, "engine": 0, "crontabs": 0, "plain": 0, "sha_mismatch": 0}

    current: str | None = None
    attrs: dict[str, str] = {}
    body: list[str] = []

    def flush() -> None:
        nonlocal current, attrs, body
        if current is None:
            return
        name, meta, content_lines = current, attrs, body
        current, attrs, body = None, {}, []

        if name in ("CONTRACT", "ENGINE-FILE", "WRAPPER-FILE"):
            abs_path = meta.get("path", "")
            if not abs_path:
                report.append(f"ERRO  {name} sem path — ignorado")
                return
            subdir = {"CONTRACT": "processes", "ENGINE-FILE": "engine", "WRAPPER-FILE": "schedulers"}[name]
            root = out / subdir
            dest = target_for_abs_path(root, abs_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            content, status = resolve_body(content_lines, meta.get("sha256"))
            dest.write_text(content, encoding="utf-8")
            key = {"CONTRACT": "contracts", "ENGINE-FILE": "engine", "WRAPPER-FILE": "wrappers"}[name]
            counters[key] += 1
            if status == "SHA256-DIVERGENTE":
                counters["sha_mismatch"] += 1
                report.append(f"ERRO  sha256 divergente: {abs_path}")
            declared = meta.get("bytes")
            if declared and declared.isdigit() and int(declared) != len(content.encode("utf-8")):
                report.append(
                    f"AVISO bytes divergentes ({declared} coletado x "
                    f"{len(content.encode('utf-8'))} reconstruído): {abs_path}"
                )
            return

        if name == "CRONTAB":
            user = meta.get("user", "desconhecido")
            source = meta.get("source", "")
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", f"{user}")
            dest = out / "crontabs" / f"user-{safe}.txt"
            header = f"### source={source}\n" if source else ""
            existing = dest.read_text(encoding="utf-8") if dest in touched else ""
            touched.add(dest)
            dest.write_text(existing + header + "\n".join(content_lines) + "\n", encoding="utf-8")
            counters["crontabs"] += 1
            return

        filename = PLAIN_SECTIONS.get(name)
        if filename is None:
            filename = name.lower().replace("-", "_") + ".txt"
            report.append(f"AVISO seção desconhecida {name} -> {filename}")
        dest_plain = out / filename
        prev = dest_plain.read_text(encoding="utf-8") if dest_plain in touched else ""
        touched.add(dest_plain)
        dest_plain.write_text(prev + "\n".join(content_lines) + "\n", encoding="utf-8")
        counters["plain"] += 1

    for line in lines:
        marker = MARKER.match(line)
        if marker:
            kind, name = marker.group(1), marker.group("name")
            if kind == "BEGIN":
                if current is not None:
                    report.append(f"AVISO seção {current} sem END antes de {name}")
                    flush()
                current, attrs, body = name, parse_attrs(marker.group("attrs")), []
            else:
                if current is None:
                    report.append(f"AVISO END {name} sem BEGIN correspondente")
                elif current != name:
                    report.append(f"AVISO END {name} fecha seção {current}")
                flush()
            continue
        if current is not None:
            body.append(line)
    flush()

    # Índice de hashes dos contratos reconstruídos (insumo de contract_hash no catálogo)
    proc_root = out / "processes"
    if proc_root.is_dir():
        rows = []
        for path in sorted(proc_root.rglob("*.json")):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            rows.append(f"{digest}  /{path.relative_to(proc_root)}")
        (out / "contracts-sha256.txt").write_text("\n".join(rows) + "\n", encoding="utf-8")

    summary = [
        f"bundle............: {args.bundle}",
        f"host..............: {host}",
        f"destino...........: {out}",
        f"contratos.........: {counters['contracts']}",
        f"wrappers..........: {counters['wrappers']}",
        f"arquivos de engine: {counters['engine']}",
        f"fontes de crontab.: {counters['crontabs']}",
        f"seções de texto...: {counters['plain']}",
        f"sha256 divergentes: {counters['sha_mismatch']}",
    ]
    (out / "unpack-report.txt").write_text("\n".join(summary + [""] + report) + "\n", encoding="utf-8")

    print("\n".join(summary))
    if report:
        print("\n--- ocorrências ---")
        print("\n".join(report))

    for name in ("cron-summary.tsv", "domain-census.tsv"):
        path = out / name
        if path.is_file():
            print(f"\n--- {name} ---")
            print(path.read_text(encoding="utf-8").rstrip())

    scan = out / "secret-scan-report.txt"
    if scan.is_file() and scan.read_text(encoding="utf-8").strip():
        print(
            "\nATENÇÃO secret-scan-report.txt não está vazio: palavra-chave sensível "
            "encontrada em contrato. Escalar ao owner do domínio (invariante 5)."
        )

    return 1 if counters["sha_mismatch"] else 0


if __name__ == "__main__":
    sys.exit(main())
