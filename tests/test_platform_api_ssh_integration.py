"""`build_invocation()` × `docker/legacy/batch-wrapper.sh`, ponta a ponta.

O container do host legado (`docker compose --profile legacy up`) não builda
em todo ambiente de desenvolvimento (a imagem base puxa pacotes de um CDN que
pode estar fora do allowlist de rede) — mas a garantia que importa não é "o
container sobe", é "a linha que a API monta é a mesma que o wrapper aceita".
Isso se verifica rodando o wrapper de verdade via bash, sem docker, do mesmo
jeito que `test_legacy_wrapper.py` já faz.

Se este teste passar e o container não subir num ambiente, a garantia de
segurança continua válida — falta só verificar SSH/rede, não o contrato.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from catalog.db.models import Job
from platform_api.ssh_backend import ExecutionRequest, build_invocation

DOCKER_LEGACY = Path(__file__).resolve().parents[1] / "docker" / "legacy"
WRAPPER = DOCKER_LEGACY / "batch-wrapper.sh"
MAIN_SH = DOCKER_LEGACY / "main.sh"

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="main.sh stub depende de jq")

CONTRACT = {
    "name_process": "reportes", "client": "cliente_a", "country": "colombia",
    "environment": "prd", "description": "teste", "send_infra_mail": "NONE",
    "steps": [
        {"step": 1, "function": "download", "stop_on_failed": True, "server": "alias_a",
         "files": [{"file_name": "r_@@@YYYYMMDD@@@.csv", "remote_path": "/out", "local_path": "."}]},
    ],
}


@pytest.fixture()
def framework(tmp_path: Path) -> Path:
    root = tmp_path / "fw"
    (root / "processes" / "reportes").mkdir(parents=True)
    (root / "logs").mkdir()
    shutil.copy(MAIN_SH, root / "main.sh")
    (root / "main.sh").chmod(0o755)
    (root / "processes" / "reportes" / "prd_aaa_col_rpt.json").write_text(
        json.dumps(CONTRACT), encoding="utf-8"
    )
    return root


def _job(framework: Path) -> Job:
    return Job(
        host="srv-sftp-2", process_name="prd_aaa_col_rpt",
        contract_path=str(framework / "processes" / "reportes" / "prd_aaa_col_rpt.json"),
        domain="reportes", environment="PROD", status="active",
    )


def _run_wrapper(framework: Path, command: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ, "FW_ROOT": str(framework),
        "SSH_ORIGINAL_COMMAND": command, "SSH_CLIENT": "172.17.0.1 5555 22",
    }
    return subprocess.run(["bash", str(WRAPPER)], env=env, capture_output=True, text=True)


def test_invocacao_da_api_e_aceita_pelo_wrapper_real(framework):
    """O caso central: o que `build_invocation` produz não é uma aproximação
    do que o wrapper aceita — é EXATAMENTE aceito por ele."""
    job = _job(framework)
    linha = build_invocation(ExecutionRequest(job=job), execution_id=str(uuid.uuid4()))
    # A API manda `main.sh --process-file <absoluto>`; o wrapper, rodando com
    # este FW_ROOT, resolve `$MAIN_SH` para o mesmo caminho absoluto — troca
    # só o prefixo do executável, que o wrapper aceita nas três grafias.
    resultado = _run_wrapper(framework, linha)
    assert resultado.returncode == 0, resultado.stderr


def test_invocacao_com_todos_os_campos_e_aceita(framework):
    job = _job(framework)
    linha = build_invocation(
        ExecutionRequest(job=job, steps="1", dates_pattern="20260901", no_mail=True),
        execution_id=str(uuid.uuid4()),
    )
    resultado = _run_wrapper(framework, linha)
    assert resultado.returncode == 0, resultado.stderr


def test_execution_id_da_api_correlaciona_o_log(framework):
    job = _job(framework)
    exec_id = str(uuid.uuid4())
    linha = build_invocation(ExecutionRequest(job=job, no_mail=True), execution_id=exec_id)
    _run_wrapper(framework, linha)
    logs = list(framework.rglob(f"*{exec_id}*.log"))
    assert logs, "o wrapper deveria ter gravado o log correlacionado por execution_id"


def test_validate_only_da_api_nao_executa_step(framework):
    """`--validate-file` grava log de VALIDAÇÃO (é o comportamento real do
    `main.sh`, que sempre inicializa o log antes de checar a flag) — o que
    não pode acontecer é uma linha de STEP executado."""
    job = _job(framework)
    exec_id = str(uuid.uuid4())
    linha = build_invocation(ExecutionRequest(job=job, validate_only=True), execution_id=exec_id)
    resultado = _run_wrapper(framework, linha)
    assert resultado.returncode == 0, resultado.stderr
    logs = list((framework / "logs").rglob(f"*{exec_id}*.log"))
    assert logs, "esperava o log correlacionado por execution_id"
    conteudo = "\n".join(p.read_text() for p in logs)
    assert "validacao ok" in conteudo
    assert 'msg="step"' not in conteudo
