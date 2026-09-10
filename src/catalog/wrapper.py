"""Parser dos scripts wrapper de `schedulers/<dominio>/*.sh`.

O wrapper é a camada que o doc de arquitetura não descrevia: o cron chama o
wrapper e é **dentro dele** que `main.sh` é invocado com os parâmetros que hoje
estão hardcoded. Extrair essa invocação é o que liga linha de crontab a
contrato JSON — e o que a Platform API vai formalizar como campos tipados.

Semântica preservada (`docs/contrato-json.md`):
    --process-file X          -> job_id do catálogo
    --manual-steps Y          -> subconjunto de steps
    --dates-pattern-files Z   -> data(s)-alvo
    --validate-file           -> validação sem executar
    --no-mail                 -> suprime notificação
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field

_FLAGS_WITH_VALUE = {"--process-file", "--manual-steps", "--dates-pattern-files"}
_FLAGS_BOOLEAN = {"--no-mail", "--validate-file"}
_VAR = re.compile(r"\$\{?\w+")


@dataclass(frozen=True)
class Invocation:
    """Uma chamada de `main.sh` encontrada dentro do wrapper."""

    lineno: int
    raw: str
    main_sh: str | None = None
    process_file: str | None = None
    manual_steps: str | None = None
    dates_pattern: str | None = None
    no_mail: bool = False
    validate_file: bool = False
    unresolved: tuple[str, ...] = field(default_factory=tuple)
    flags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def process_file_is_resolved(self) -> bool:
        return bool(self.process_file) and not _VAR.search(self.process_file or "")


@dataclass(frozen=True)
class WrapperScript:
    path: str
    invocations: tuple[Invocation, ...]
    variables: dict[str, str] = field(default_factory=dict)
    flags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def process_files(self) -> tuple[str, ...]:
        return tuple(i.process_file for i in self.invocations if i.process_file)


def parse_wrapper(path: str, text: str) -> WrapperScript:
    """Extrai as invocações de `main.sh` e as atribuições de variável do wrapper."""
    variables: dict[str, str] = {}
    invocations: list[Invocation] = []
    flags: list[str] = []

    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        assign = re.match(r"^(?:export\s+)?([A-Za-z_]\w*)=(.*)$", stripped)
        if assign and "main.sh" not in stripped:
            variables[assign.group(1)] = assign.group(2).strip().strip('"').strip("'")
            continue

        if "main.sh" not in stripped:
            continue

        invocations.append(_parse_invocation(lineno, stripped, variables))

    if not invocations:
        flags.append("sem-invocacao-de-main-sh")
    if len(invocations) > 1:
        flags.append(f"multiplas-invocacoes:{len(invocations)}")

    return WrapperScript(path=path, invocations=tuple(invocations), variables=variables, flags=tuple(flags))


def _parse_invocation(lineno: int, line: str, variables: dict[str, str]) -> Invocation:
    try:
        tokens = shlex.split(line, comments=True)
    except ValueError:
        # aspas desbalanceadas no wrapper: cai para a divisão ingênua e sinaliza
        tokens = line.split()
        return _from_tokens(lineno, line, tokens, variables, extra_flags=("aspas-desbalanceadas",))
    return _from_tokens(lineno, line, tokens, variables)


def _from_tokens(
    lineno: int,
    raw: str,
    tokens: list[str],
    variables: dict[str, str],
    extra_flags: tuple[str, ...] = (),
) -> Invocation:
    values: dict[str, str] = {}
    booleans: set[str] = set()
    unresolved: list[str] = []
    main_sh = next((t for t in tokens if t.endswith("main.sh")), None)

    index = 0
    while index < len(tokens):
        token = tokens[index]
        name, sep, inline_value = token.partition("=")
        if name in _FLAGS_WITH_VALUE:
            if sep and inline_value:
                values[name] = inline_value
            elif index + 1 < len(tokens):
                values[name] = tokens[index + 1]
                index += 1
        elif token in _FLAGS_BOOLEAN:
            booleans.add(token)
        index += 1

    resolved: dict[str, str] = {}
    for flag, value in values.items():
        expanded = _expand(value, variables)
        if _VAR.search(expanded):
            unresolved.append(f"{flag}={value}")
        resolved[flag] = expanded

    return Invocation(
        lineno=lineno,
        raw=raw,
        main_sh=main_sh,
        process_file=resolved.get("--process-file"),
        manual_steps=resolved.get("--manual-steps"),
        dates_pattern=resolved.get("--dates-pattern-files"),
        no_mail="--no-mail" in booleans,
        validate_file="--validate-file" in booleans,
        unresolved=tuple(unresolved),
        flags=extra_flags,
    )


def _expand(value: str, variables: dict[str, str]) -> str:
    """Expande `$VAR`/`${VAR}` com as atribuições vistas no próprio wrapper."""
    result = value
    for _ in range(5):  # variável que referencia variável, com limite
        before = result
        for name, replacement in variables.items():
            result = result.replace(f"${{{name}}}", replacement).replace(f"${name}", replacement)
        if result == before:
            break
    return result
