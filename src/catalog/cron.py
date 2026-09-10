"""Parser de linhas de crontab do legado.

Regras que vêm do inventário real e não podem ser perdidas:

* Linha comentada que contém comando é **feature flag** (job desabilitado), não
  documentação. O comentário de prosa imediatamente acima dela é o motivo, e
  vira `status_reason` no catálogo (requisito 2 do SPEC).
* Nada é descartado: linha que não casa com nada vira `kind="unparsed"` com o
  motivo. O total tem de fechar com a contagem do arquivo.
* O comando é o primeiro caminho `.sh` da linha — nunca o do redirecionamento
  de log, que aparece na mesma linha (`>> .../logs/.../x.log`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

# 5 campos de agenda, ou macro @daily/@reboot/...
_SCHEDULE_5 = re.compile(
    r"^\s*(?P<schedule>(?:[^\s]+\s+){4}[^\s]+)\s+(?P<command>\S.*)$"
)
_SCHEDULE_MACRO = re.compile(r"^\s*(?P<schedule>@\w+)\s+(?P<command>\S.*)$")
_ENV_ASSIGN = re.compile(r"^\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*)$")
_SH_PATH = re.compile(r"/[A-Za-z0-9_@.+\-/]*\.sh")
_LOOKS_LIKE_COMMAND = re.compile(r"(\.sh|\.py|\.jar|/bin/|crontab\s)")


class EntryKind(str, Enum):
    JOB = "job"                  # agenda + script do framework
    MAINTENANCE = "maintenance"  # agenda + comando que não é do framework
    ENV = "env"                  # VAR=valor
    COMMENT = "comment"          # comentário de documentação
    BLANK = "blank"
    UNPARSED = "unparsed"


@dataclass(frozen=True)
class CronEntry:
    source: str                 # arquivo/fonte (ex. "user-batch_user")
    lineno: int
    raw: str
    kind: EntryKind
    enabled: bool = True
    schedule: str | None = None
    command: str | None = None
    script_path: str | None = None
    log_path: str | None = None
    inline_comment: str | None = None
    status_reason: str | None = None   # prosa dos comentários acima da entrada
    env_name: str | None = None
    env_value: str | None = None
    reason: str | None = None         # por que ficou unparsed
    flags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_schedulable(self) -> bool:
        return self.kind in (EntryKind.JOB, EntryKind.MAINTENANCE)


def parse_crontab(text: str, source: str = "crontab") -> list[CronEntry]:
    """Converte o texto de um crontab na sequência completa de entradas."""
    entries: list[CronEntry] = []
    pending_prose: list[str] = []

    for lineno, raw in enumerate(text.splitlines(), start=1):
        if raw.startswith("### source="):  # cabeçalho inserido pelo desempacotador
            continue

        stripped = raw.strip()

        if not stripped:
            entries.append(CronEntry(source, lineno, raw, EntryKind.BLANK))
            pending_prose.clear()
            continue

        if stripped.startswith("#"):
            body = stripped.lstrip("#").strip()
            embedded = _find_schedule_in_comment(body) if _LOOKS_LIKE_COMMAND.search(body) else None
            if embedded is not None:
                inline_reason, remainder = embedded
                reasons = [*pending_prose]
                if inline_reason:
                    reasons.append(inline_reason)
                entry = _build_scheduled(
                    source, lineno, raw, remainder, enabled=False,
                    status_reason="\n".join(reasons) or None,
                )
                entries.append(entry)
                pending_prose.clear()
            elif (on_demand := _on_demand_job(source, lineno, raw, body, pending_prose)) is not None:
                entries.append(on_demand)
                pending_prose.clear()
            else:
                entries.append(
                    CronEntry(source, lineno, raw, EntryKind.COMMENT, inline_comment=body)
                )
                if body:
                    pending_prose.append(body)
            continue

        # Atribuição de ambiente (CRON_TZ, PATH, MAILTO...) precede a agenda
        if _ENV_ASSIGN.match(stripped) and not _parse_schedule(stripped):
            match = _ENV_ASSIGN.match(stripped)
            assert match is not None
            entries.append(
                CronEntry(
                    source, lineno, raw, EntryKind.ENV,
                    env_name=match.group("name"), env_value=match.group("value").strip(),
                )
            )
            pending_prose.clear()
            continue

        if _parse_schedule(stripped):
            entries.append(
                _build_scheduled(
                    source, lineno, raw, stripped, enabled=True,
                    status_reason="\n".join(pending_prose) or None,
                )
            )
            pending_prose.clear()
            continue

        entries.append(
            CronEntry(source, lineno, raw, EntryKind.UNPARSED, reason="agenda nao reconhecida")
        )
        pending_prose.clear()

    return entries


_MONTHS = {"jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"}
_DOWS = {"sun", "mon", "tue", "wed", "thu", "fri", "sat"}


def _parse_schedule(line: str) -> tuple[str, str] | None:
    match = _SCHEDULE_MACRO.match(line)
    if match:
        return match.group("schedule").strip(), match.group("command").strip()

    match = _SCHEDULE_5.match(line)
    if not match:
        return None
    schedule = match.group("schedule").strip()
    fields = schedule.split()
    # Sem esta validação, qualquer linha com 6 palavras passaria por agenda
    # (ex.: uma frase solta no crontab viraria job).
    if len(fields) != 5 or not all(_valid_field(f, i) for i, f in enumerate(fields)):
        return None
    return schedule, match.group("command").strip()


def _on_demand_job(
    source: str, lineno: int, raw: str, body: str, pending_prose: list[str]
) -> CronEntry | None:
    """Linha comentada com wrapper do framework e SEM agenda = job on-demand.

    O legado registra assim os jobs que existem e são disparados à mão (o SPEC
    fala de "119 desabilitadas/on-demand"). Não inventamos agenda: entram com
    `schedule=None` e a flag `on-demand`.
    """
    normalized = re.sub(r"#+", " ", body).strip()
    paths = _SH_PATH.findall(normalized)
    wrapper = next((p for p in paths if "/schedulers/" in p), None)
    if wrapper is None:
        return None

    log_match = re.search(r">>?\s*(?P<log>\S+)", normalized)
    reason = normalized.split(wrapper)[0].strip(" -:#") or None
    reasons = [*pending_prose, *( [reason] if reason else [] )]

    return CronEntry(
        source=source,
        lineno=lineno,
        raw=raw,
        kind=EntryKind.JOB,
        enabled=False,
        schedule=None,
        command=normalized,
        script_path=wrapper,
        log_path=log_match.group("log") if log_match else None,
        status_reason="\n".join(reasons) or None,
        flags=("on-demand",),
    )


def _find_schedule_in_comment(body: str) -> tuple[str | None, str] | None:
    """Localiza a agenda dentro de uma linha comentada e separa o motivo.

    O legado funde a razão da desabilitação com a própria entrada, inclusive o
    ticket, e às vezes cola os `#` no primeiro campo da agenda:

        #### DESINCORPORADO #### CDSI-162 00 09 * * * /opt2/.../x.sh >> ...
        ###NP-16498####20 03 * * *  /opt2/.../y.sh >> ...

    Devolve (motivo, "agenda + comando"). O motivo vira `status_reason`.
    """
    normalized = re.sub(r"#+", " ", body).strip()
    tokens = normalized.split()
    if len(tokens) < 6:
        return None

    for start in range(0, len(tokens) - 5):
        window = tokens[start : start + 5]
        if not all(_valid_field(f, i) for i, f in enumerate(window)):
            continue
        remainder_tokens = tokens[start + 5 :]
        remainder = " ".join(remainder_tokens)
        if not remainder:
            continue
        if not (remainder.startswith("/") or _LOOKS_LIKE_COMMAND.search(remainder)):
            continue
        reason = " ".join(tokens[:start]).strip(" -:") or None
        return reason, f"{' '.join(window)} {remainder}"

    return None


def _valid_field(value: str, index: int) -> bool:
    """Valida um campo de agenda: `*`, número, lista, faixa, passo ou nome (mês/dia)."""
    if not value:
        return False
    for item in value.split(","):
        base = item.split("/")[0]
        for token in base.split("-"):
            if token in ("*", "?", ""):
                continue
            if token.isdigit():
                continue
            low = token.lower()
            if index == 3 and low in _MONTHS:
                continue
            if index == 4 and low in _DOWS:
                continue
            return False
    return True


def _build_scheduled(
    source: str, lineno: int, raw: str, line: str, *, enabled: bool, status_reason: str | None
) -> CronEntry:
    parsed = _parse_schedule(line)
    assert parsed is not None
    schedule, command = parsed

    flags: list[str] = []

    # No crontab de sistema (/etc/crontab, cron.d) existe um campo de usuário
    # entre a agenda e o comando.
    run_as: str | None = None
    first, _, rest = command.partition(" ")
    if first and not first.startswith("/") and not first.startswith("@") and rest.strip().startswith("/"):
        run_as = first
        command = rest.strip()
        flags.append(f"run_as:{run_as}")

    script_path = None
    log_path = None
    paths = _SH_PATH.findall(command)
    if paths:
        script_path = paths[0]
    log_match = re.search(r">>?\s*(?P<log>\S+)", command)
    if log_match:
        log_path = log_match.group("log")

    inline = None
    hash_pos = command.find("#")
    if hash_pos != -1:
        # o legado usa '##comentario' com frequência: remove todos os '#' do início
        inline = command[hash_pos:].lstrip("#").strip() or None
        command = command[:hash_pos].strip()

    if script_path and "/schedulers/" in script_path:
        kind = EntryKind.JOB
    elif script_path or _LOOKS_LIKE_COMMAND.search(command):
        kind = EntryKind.MAINTENANCE
    else:
        kind = EntryKind.MAINTENANCE
        flags.append("comando-sem-script")

    return CronEntry(
        source=source,
        lineno=lineno,
        raw=raw,
        kind=kind,
        enabled=enabled,
        schedule=schedule,
        command=command,
        script_path=script_path,
        log_path=log_path,
        inline_comment=inline,
        status_reason=status_reason,
        flags=tuple(flags),
    )
