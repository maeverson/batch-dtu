"""Leitor do pacote de coleta produzido por `seed/collect/collect-seed.sh`.

Estrutura esperada:

    batch-seed-<host>-<stamp>/
        inventory.txt   secoes "##### BEGIN <NOME> | k=v" / "##### END <NOME>"
        framework/      arvore relativa a raiz do framework (processes/, schedulers/, main.sh)
        SHA256SUMS

Nada aqui interpreta conteúdo de job: só entrega as seções e os arquivos.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

MARKER = re.compile(r"^##### (BEGIN|END) (?P<name>[A-Z0-9-]+)(?P<attrs>.*)$")


@dataclass
class Section:
    name: str
    attrs: dict[str, str]
    lines: list[str]

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def tsv(self) -> list[list[str]]:
        return [line.split("\t") for line in self.lines if line.strip()]

    def key_values(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for row in self.tsv():
            if len(row) >= 2:
                out[row[0]] = row[1]
        return out


class SeedPackage:
    """Pacote coletado, já extraído em disco."""

    def __init__(self, root: Path) -> None:
        self.root = root
        inventory = root / "inventory.txt"
        if not inventory.is_file():
            raise FileNotFoundError(f"inventory.txt ausente em {root}")
        self.sections = _parse_sections(inventory.read_text(encoding="utf-8", errors="replace"))

    # -- metadados -----------------------------------------------------------
    @property
    def host(self) -> str:
        return self.first("HOST-INFO").key_values().get("hostname_short", "desconhecido")

    @property
    def host_info(self) -> dict[str, str]:
        return self.first("HOST-INFO").key_values()

    @property
    def detected_paths(self) -> dict[str, str]:
        return self.first("DETECTED-PATHS").key_values()

    @property
    def framework_root(self) -> str:
        return self.detected_paths.get("framework_root", "")

    @property
    def timezone(self) -> str | None:
        """TZ do host: o crontab não declara, então vem de /etc/localtime."""
        localtime = self.host_info.get("etc_localtime", "")
        if "/zoneinfo/" in localtime:
            return localtime.split("/zoneinfo/", 1)[1]
        return self.host_info.get("etc_timezone") or None

    # -- seções --------------------------------------------------------------
    def all(self, name: str) -> list[Section]:
        return [s for s in self.sections if s.name == name]

    def first(self, name: str) -> Section:
        found = self.all(name)
        return found[0] if found else Section(name, {}, [])

    def crontabs(self) -> list[tuple[str, str]]:
        """[(fonte, texto)] — uma entrada por crontab/arquivo de cron coletado."""
        out: list[tuple[str, str]] = []
        for section in self.all("CRONTAB"):
            user = section.attrs.get("user", "desconhecido")
            source = section.attrs.get("source", "")
            label = f"{user}@{source}" if source else user
            out.append((label, section.text))
        return out

    # -- arquivos ------------------------------------------------------------
    @property
    def framework_dir(self) -> Path:
        return self.root / "framework"

    def local_path(self, absolute: str) -> Path | None:
        """Converte caminho absoluto do servidor no caminho dentro do pacote."""
        fw = self.framework_root
        if not fw or not absolute.startswith(fw.rstrip("/") + "/"):
            return None
        relative = absolute[len(fw.rstrip("/")) + 1 :]
        candidate = self.framework_dir / relative
        return candidate if candidate.is_file() else None

    def contracts(self) -> list[Path]:
        base = self.framework_dir / "processes"
        return sorted(base.rglob("*.json")) if base.is_dir() else []

    def wrappers(self) -> list[Path]:
        base = self.framework_dir / "schedulers"
        return sorted(base.rglob("*.sh")) if base.is_dir() else []

    def server_path(self, local: Path) -> str:
        """Caminho absoluto no servidor a partir do caminho local no pacote."""
        relative = local.relative_to(self.framework_dir)
        return f"{self.framework_root.rstrip('/')}/{relative.as_posix()}"


def _parse_sections(text: str) -> list[Section]:
    sections: list[Section] = []
    current: Section | None = None

    for line in text.splitlines():
        marker = MARKER.match(line)
        if marker:
            kind, name = marker.group(1), marker.group("name")
            if kind == "BEGIN":
                current = Section(name, _parse_attrs(marker.group("attrs")), [])
                sections.append(current)
            else:
                current = None
            continue
        if current is not None:
            current.lines.append(line)

    return sections


def _parse_attrs(raw: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for part in raw.split("|"):
        part = part.strip()
        if "=" in part:
            key, _, value = part.partition("=")
            attrs[key.strip()] = value.strip()
    return attrs


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()
