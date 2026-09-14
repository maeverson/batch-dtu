"""`ExecutionBackend` — a fronteira que isola o mecanismo de execução da Fase 1.

A Platform API monta a invocação **só de campos tipados e validados contra o
catálogo** (`docs/seguranca.md`, invariante da API): nunca concatena entrada
livre do usuário. `build_invocation` é a função que faz isso, e ela é pura —
testável sem SSH, sem container, sem rede — porque a segurança deste caminho
não pode depender de infraestrutura estar de pé para ser verificada.

O validador é redundante com `docker/legacy/batch-wrapper.sh` DE PROPÓSITO: o
wrapper revalida do lado do servidor porque a API pode ter bug; a API valida
aqui porque não pode depender só do wrapper. Duas camadas, mesmo padrão —
nenhuma delas confia na outra.

`ExecutionBackend` é a interface que a Fase 2 troca (SSH → orchestrator) sem
o resto da API perceber (`modules/platform-api/SPEC.md`).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Protocol

from catalog.db.models import Job

# Mesmos padrões do wrapper (`docker/legacy/batch-wrapper.sh`) — divergir dos
# dois seria abrir uma classe de entrada que um lado aceita e o outro recusa.
_MANUAL_STEPS_RE = re.compile(r"^[0-9]+([,-][0-9]+)*$")
_DATES_NUMERIC_RE = re.compile(r"^[0-9]{8}([,][0-9]{8})*$")
_DATES_ISO_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}([,][0-9]{4}-[0-9]{2}-[0-9]{2})*$")
_EXECUTION_ID_RE = re.compile(r"^[0-9a-fA-F-]{8,64}$")

# Qualquer um destes num campo individual, ou na linha montada, é recusado —
# mesma lista do wrapper (passo 1 de `batch-wrapper.sh`). É defesa em
# profundidade: os campos já vêm validados por regex própria; isto pega
# qualquer coisa que uma regex tenha deixado passar por engano de quem mexer
# neste arquivo no futuro.
_SHELL_METACHARACTERS = re.compile(r"[;|&$`><(){}\n\r*?~\\]")


class InvalidExecutionRequest(ValueError):
    """Campo fora do formato aceito. Nunca é corrigido em silêncio —
    a requisição inteira é recusada."""


@dataclass(frozen=True)
class ExecutionRequest:
    job: Job
    steps: str | None = None            # semântica --manual-steps
    dates_pattern: str | None = None     # semântica --dates-pattern-files
    no_mail: bool = False
    validate_only: bool = False          # semântica --validate-file


def _validar_campo(valor: str, padrao: re.Pattern, nome: str) -> str:
    if not padrao.match(valor):
        raise InvalidExecutionRequest(f"{nome} fora do formato aceito: {valor!r}")
    if _SHELL_METACHARACTERS.search(valor):
        # Nunca deveria chegar aqui se o padrão acima está certo — mas o
        # invariante é "nenhum caminho de código concatena entrada sem
        # validar", não "confiamos que esta regex está certa para sempre".
        raise InvalidExecutionRequest(f"{nome} contém caractere não permitido: {valor!r}")
    return valor


def build_invocation(request: ExecutionRequest, execution_id: str | None = None) -> str:
    """Monta a linha exata que vai para `SSH_ORIGINAL_COMMAND`.

    Levanta `InvalidExecutionRequest` para qualquer campo fora do formato —
    nunca sanitiza, nunca ignora, nunca tenta "consertar" a entrada.
    """
    job = request.job
    if not job.contract_path:
        raise InvalidExecutionRequest(f"job {job.process_name} não tem contrato resolvido")
    if "/processes/" not in job.contract_path or ".." in job.contract_path:
        # Redundante com o wrapper (checagem 4 de batch-wrapper.sh) — mas o
        # dado vem do catálogo, que é escrito por import/load; um bug lá não
        # pode virar travessia de diretório aqui.
        raise InvalidExecutionRequest(
            f"contract_path fora de processes/ ou com travessia: {job.contract_path!r}"
        )

    execution_id = execution_id or str(uuid.uuid4())
    _validar_campo(execution_id, _EXECUTION_ID_RE, "execution_id")

    partes = ["main.sh", "--process-file", job.contract_path]

    if request.steps:
        _validar_campo(request.steps, _MANUAL_STEPS_RE, "steps")
        partes += ["--manual-steps", request.steps]

    if request.dates_pattern:
        if not (_DATES_NUMERIC_RE.match(request.dates_pattern)
                or _DATES_ISO_RE.match(request.dates_pattern)):
            raise InvalidExecutionRequest(
                f"dates_pattern fora do formato aceito: {request.dates_pattern!r}"
            )
        if _SHELL_METACHARACTERS.search(request.dates_pattern):
            raise InvalidExecutionRequest(f"dates_pattern contém caractere não permitido")
        partes += ["--dates-pattern-files", request.dates_pattern]

    if request.no_mail:
        partes.append("--no-mail")
    if request.validate_only:
        partes.append("--validate-file")

    # `--execution-id` é lido pelo WRAPPER (nomeia o log), não pelo main.sh —
    # ver docs/api/platform-api.md e docker/legacy/batch-wrapper.sh passo 6.
    partes += ["--execution-id", execution_id]

    linha = " ".join(partes)
    if _SHELL_METACHARACTERS.search(linha):
        raise InvalidExecutionRequest("linha montada contém caractere de shell — abortando")
    return linha


@dataclass(frozen=True)
class ExecutionResult:
    execution_id: str
    exit_code: int
    stdout: str
    stderr: str

    @property
    def result(self) -> str:
        return "success" if self.exit_code == 0 else "failure"


class ExecutionBackend(Protocol):
    """A interface que sobrevive à troca SSH → orchestrator (Fase 2)."""

    async def dispatch(self, request: ExecutionRequest, execution_id: str) -> ExecutionResult: ...


class SSHExecutionBackend:
    """Backend Fase 1: SSH parametrizado contra `backoffice_svc` + `command=`.

    Nunca abre shell interativo, nunca usa `asyncssh`'s `check=True` com
    string interpolada por fora de `build_invocation` — a única linha que
    trafega é o retorno dessa função.
    """

    def __init__(self, *, host: str, port: int, username: str, key_path: str,
                 connect_timeout: float = 10.0, known_hosts: str | None = None):
        self._host = host
        self._port = port
        self._username = username
        self._key_path = key_path
        self._connect_timeout = connect_timeout
        self._known_hosts = known_hosts

    async def dispatch(self, request: ExecutionRequest, execution_id: str) -> ExecutionResult:
        import asyncssh

        linha = build_invocation(request, execution_id)
        async with asyncssh.connect(
            self._host, port=self._port, username=self._username,
            client_keys=[self._key_path], known_hosts=self._known_hosts,
            connect_timeout=self._connect_timeout,
        ) as conn:
            processo = await conn.run(linha, check=False)
        return ExecutionResult(
            execution_id=execution_id,
            exit_code=processo.returncode if processo.returncode is not None else -1,
            stdout=str(processo.stdout or ""),
            stderr=str(processo.stderr or ""),
        )


class InMemoryExecutionBackend:
    """Para teste de endpoint sem SSH nenhum. Grava cada requisição recebida
    para o teste inspecionar o QUE foi montado, e devolve um resultado
    programável (default: sucesso)."""

    def __init__(self, *, exit_code: int = 0, stdout: str = "ok", stderr: str = ""):
        self.calls: list[tuple[ExecutionRequest, str]] = []
        self._exit_code = exit_code
        self._stdout = stdout
        self._stderr = stderr

    async def dispatch(self, request: ExecutionRequest, execution_id: str) -> ExecutionResult:
        # A validação roda igual ao backend real — um teste que usa este
        # backend não pode aceitar requisição que o SSH real recusaria.
        build_invocation(request, execution_id)
        self.calls.append((request, execution_id))
        return ExecutionResult(
            execution_id=execution_id, exit_code=self._exit_code,
            stdout=self._stdout, stderr=self._stderr,
        )
