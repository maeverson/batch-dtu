"""Schema do contrato JSON e validação na escrita.

O schema aqui **não é aspiracional**: foi derivado dos 575 contratos reais da
coleta de 09/2026 e aceita 100% deles (ver `tests/test_contract_validation.py`).
Isso é exigência do invariante 1 — o contrato é interface estável, e nenhum
schema nosso pode transformar em "inválido" um job que roda em produção hoje.

Consequências desse compromisso, todas deliberadas:

* `schema_version` é **opcional**. Nenhum contrato do parque tem o campo; ele é
  a extensão futura do invariante 1, então ausência é legado, nunca erro.
* Campo desconhecido é **aviso**, não erro. Extensões são aditivas; recusar o
  que não conhecemos quebraria a próxima extensão antes de ela existir.
* O que é erro é o que o executor não consegue executar: função desconhecida,
  step sem o campo que a função exige, tipo errado.

Duas políticas, porque escrever no catálogo e importar o legado têm exigências
opostas:

* `Policy.LEGACY` — import/seed. Valida e **registra**; nunca bloqueia. Um
  contrato quebrado em disco é um fato do parque, e o catálogo existe para
  mostrá-lo.
* `Policy.STRICT` — escrita nova (Platform API, Fase 1). Erro levanta
  `ContractInvalid` e a escrita não acontece.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

# Versão DO SCHEMA (deste arquivo), não do contrato. Muda quando as regras
# mudam, para que um veredito persistido diga contra o que foi julgado.
SCHEMA_ID = "batch-dtu/contrato-json"
SCHEMA_REVISION = "1"

# As 9 funções observadas nos 3383 steps do parque. Função fora desta lista o
# `main.sh` não sabe despachar — é erro, não extensão.
STEP_FUNCTIONS: dict[str, tuple[str, ...]] = {
    "download": ("server", "files"),
    "upload": ("server", "files"),
    "download_remote": ("server_remote", "files"),
    "upload_remote": ("server_remote", "files"),
    "copy_local": ("files",),
    "execute_command": ("command",),
    "encrypt": ("key", "files"),
    "decrypt": ("key", "files"),
    "send_mail": ("mails", "subject", "content"),
}

TOP_LEVEL_FIELDS = frozenset({
    "name_process", "client", "country", "environment", "description",
    "send_infra_mail", "steps", "additional_info", "schema_version",
})

# Chaves de um item de `files`. `file_name` está nos 3368 itens do parque; o
# resto varia por função (`copy_local` usa source_path/destination_path, as
# remotas usam remote_path/local_path).
FILE_FIELDS = frozenset({
    "file_name", "source", "source_path", "destination_path", "local_path",
    "remote_path", "remove_on_source", "min_lines", "armored",
})

STEP_FIELDS = frozenset({
    "step", "function", "stop_on_failed", "files", "server", "server_remote",
    "command", "arguments", "key", "mails", "subject", "content",
    "content_type", "attachment", "list_files", "permissions",
})

SEND_INFRA_MAIL = ("BOTH", "SUCCESS", "ERROR", "NONE")


def _file_schema() -> dict[str, Any]:
    return {
        "type": "object",
        # Um arquivo sem nome o executor não sabe buscar nem entregar. É o
        # único campo presente em 100% dos itens do parque.
        "required": ["file_name"],
        "properties": {
            "file_name": {"type": "string"},
            "source": {"type": "string"},
            "source_path": {"type": "string"},
            "destination_path": {"type": "string"},
            "local_path": {"type": "string"},
            "remote_path": {"type": "string"},
            "remove_on_source": {"type": "boolean"},
            "min_lines": {"type": "integer"},
            "armored": {"type": "boolean"},
        },
        "additionalProperties": True,
    }


def _step_schema() -> dict[str, Any]:
    conditionals = []
    for function, required in STEP_FUNCTIONS.items():
        conditionals.append({
            "if": {"properties": {"function": {"const": function}}, "required": ["function"]},
            "then": {"required": list(required)},
        })
    return {
        "type": "object",
        "required": ["step", "function", "stop_on_failed"],
        "properties": {
            "step": {"type": "integer", "minimum": 1},
            "function": {"type": "string", "enum": sorted(STEP_FUNCTIONS)},
            "stop_on_failed": {"type": "boolean"},
            "files": {"type": "array", "items": _file_schema()},
            "server": {"type": "string"},
            "server_remote": {"type": "string"},
            "command": {"type": "string"},
            "arguments": {"type": "string"},
            "key": {"type": "string"},
            "mails": {"type": "string"},
            "subject": {"type": "string"},
            "content": {"type": "string"},
            "content_type": {"type": "string"},
            "attachment": {"type": "string"},
            "list_files": {"type": "boolean"},
            "permissions": {"type": "string"},
        },
        "allOf": conditionals,
    }


CONTRACT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": SCHEMA_ID,
    "title": "Contrato JSON de job (Batch DTU)",
    "type": "object",
    "required": [
        "name_process", "client", "country", "environment",
        "description", "send_infra_mail", "steps",
    ],
    "properties": {
        "name_process": {"type": "string", "minLength": 1},
        "client": {"type": "string", "minLength": 1},
        "country": {"type": "string", "minLength": 1},
        "environment": {"type": "string", "minLength": 1},
        "description": {"type": "string"},
        "send_infra_mail": {"type": "string", "enum": list(SEND_INFRA_MAIL)},
        "additional_info": {"type": "string"},
        "schema_version": {"type": "string"},
        "steps": {"type": "array", "minItems": 1, "items": _step_schema()},
    },
    # Aditivo por decisão: ver docstring.
    "additionalProperties": True,
}


class Policy(str, Enum):
    LEGACY = "legacy"   # import/seed: registra, não bloqueia
    STRICT = "strict"   # escrita nova: erro bloqueia


class Severity(str, Enum):
    ERROR = "erro"
    WARNING = "aviso"


@dataclass(frozen=True)
class Violation:
    path: str          # ponteiro no contrato: "steps[3].server"
    rule: str
    message: str
    severity: Severity = Severity.ERROR

    def as_dict(self) -> dict[str, str]:
        return {
            "path": self.path, "rule": self.rule,
            "message": self.message, "severity": self.severity.value,
        }


@dataclass(frozen=True)
class ValidationReport:
    violations: tuple[Violation, ...] = ()
    schema_revision: str = SCHEMA_REVISION

    @property
    def errors(self) -> tuple[Violation, ...]:
        return tuple(v for v in self.violations if v.severity is Severity.ERROR)

    @property
    def warnings(self) -> tuple[Violation, ...]:
        return tuple(v for v in self.violations if v.severity is Severity.WARNING)

    @property
    def valid(self) -> bool:
        return not self.errors

    @property
    def status(self) -> str:
        if self.errors:
            return "invalid"
        return "valid_with_warnings" if self.warnings else "valid"

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_ID,
            "schema_revision": self.schema_revision,
            "status": self.status,
            "violations": [v.as_dict() for v in self.violations],
        }

    def summary(self) -> str:
        if not self.violations:
            return "contrato valido"
        parts = []
        for v in self.violations[:5]:
            parts.append(f"{v.path}: {v.message}")
        extra = len(self.violations) - len(parts)
        return "; ".join(parts) + (f"; e mais {extra}" if extra > 0 else "")


class ContractInvalid(Exception):
    """Contrato recusado sob `Policy.STRICT`."""

    def __init__(self, report: ValidationReport):
        self.report = report
        super().__init__(report.summary())


def _pointer(path) -> str:
    out = ""
    for part in path:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out or "(raiz)"


def validate_contract(contract: Any) -> ValidationReport:
    """Valida um contrato já desserializado. Nunca levanta — devolve veredito."""
    from jsonschema import Draft202012Validator

    if not isinstance(contract, dict):
        return ValidationReport((Violation(
            "(raiz)", "tipo", "raiz do contrato nao e objeto JSON"),))

    violations: list[Violation] = []
    validator = Draft202012Validator(CONTRACT_SCHEMA)
    for error in sorted(validator.iter_errors(contract), key=lambda e: list(e.absolute_path)):
        # `allOf` condicional produz um erro-guarda-chuva além do erro real;
        # o específico basta, e o genérico só polui o relatório.
        if error.validator == "allOf":
            continue
        violations.append(Violation(
            _pointer(error.absolute_path), str(error.validator), error.message))

    violations.extend(_lint(contract))
    return ValidationReport(tuple(violations))


def _lint(contract: dict) -> list[Violation]:
    """Regras que não cabem no JSON Schema — todas aviso, nenhuma bloqueia."""
    out: list[Violation] = []

    for field in sorted(set(contract) - TOP_LEVEL_FIELDS):
        out.append(Violation(
            field, "campo-desconhecido",
            f"campo '{field}' nao faz parte do contrato conhecido",
            Severity.WARNING))

    steps = contract.get("steps")
    if not isinstance(steps, list):
        return out

    numbers: list[int] = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        where = f"steps[{index}]"
        for field in sorted(set(step) - STEP_FIELDS):
            # `commands` (lista) aparece uma vez no parque, onde a função pede
            # `command` (string): campo vizinho que o executor ignora em
            # silêncio. Vale aviso nomeado, não erro genérico.
            out.append(Violation(
                f"{where}.{field}", "campo-desconhecido",
                f"campo '{field}' nao faz parte do step conhecido",
                Severity.WARNING))
        for item_index, item in enumerate(step.get("files") or []):
            if not isinstance(item, dict):
                continue
            for field in sorted(set(item) - FILE_FIELDS):
                out.append(Violation(
                    f"{where}.files[{item_index}].{field}", "campo-desconhecido",
                    f"campo '{field}' nao faz parte do item de arquivo conhecido",
                    Severity.WARNING))
        if isinstance(step.get("step"), int):
            numbers.append(step["step"])

    if numbers and numbers != list(range(1, len(numbers) + 1)):
        out.append(Violation(
            "steps", "numeracao-nao-sequencial",
            f"numeros de step {numbers} nao formam 1..{len(numbers)}",
            Severity.WARNING))

    return out


def assert_valid(contract: Any, policy: Policy = Policy.STRICT) -> ValidationReport:
    """Valida aplicando a política. Sob STRICT, erro levanta `ContractInvalid`."""
    report = validate_contract(contract)
    if policy is Policy.STRICT and not report.valid:
        raise ContractInvalid(report)
    return report
