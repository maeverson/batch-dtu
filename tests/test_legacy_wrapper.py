"""Suíte do host legado simulado (`docker/legacy/`).

Vale como teste de regressão de dois controles da Fase 1:

* `batch-wrapper.sh` é o alvo do `command=` no `authorized_keys` do
  `backoffice_svc` e **revalida tudo server-side** (`docs/seguranca.md`). Se ele
  passar a aceitar algo que não seja `main.sh` com flags conhecidas, a premissa
  "a API nunca interpola shell arbitrário" deixa de ter rede de proteção.
* `main.sh` (stub) precisa preservar a semântica que a API expõe: steps
  sequenciais com `stop_on_failed`, placeholders de data, `--manual-steps`,
  múltiplas datas serializadas e `execution_id` no nome do log.

Roda direto no bash, sem docker.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

DOCKER_LEGACY = Path(__file__).resolve().parents[1] / "docker" / "legacy"
WRAPPER = DOCKER_LEGACY / "batch-wrapper.sh"
MAIN_SH = DOCKER_LEGACY / "main.sh"

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="main.sh stub depende de jq")

CONTRACT = {
    "schema_version": "1.0",
    "steps": [
        {"type": "download", "connection": "alias_a", "file": "r_@@@YYYYMMDD@@@.csv", "stop_on_failed": True},
        {"type": "copy_local", "file": "r_@@@YYYY@@@_@@@JULIANO@@@.csv", "stop_on_failed": False},
        {"type": "upload_remote", "connection": "alias_b", "stop_on_failed": False, "dev_fail": True},
        {"type": "send_mail", "stop_on_failed": False},
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
    (root / "processes" / "reportes" / "invalido.json").write_text('{"steps": [', encoding="utf-8")
    return root


def run_wrapper(framework: Path, command: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "FW_ROOT": str(framework),
        "SSH_ORIGINAL_COMMAND": command,
        "SSH_CLIENT": "172.17.0.1 5555 22",
    }
    return subprocess.run(
        ["bash", str(WRAPPER)], env=env, capture_output=True, text=True, check=False
    )


def run_main(framework: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(framework / "main.sh"), *args],
        env={**os.environ, "FW_ROOT": str(framework)},
        capture_output=True, text=True, check=False,
    )


def contract(framework: Path, name: str = "prd_aaa_col_rpt.json") -> str:
    return str(framework / "processes" / "reportes" / name)


# --- wrapper: caminho permitido ---------------------------------------------

def test_wrapper_permite_validacao(framework):
    result = run_wrapper(framework, f"{framework}/main.sh --process-file {contract(framework)} --validate-file")
    assert result.returncode == 0, result.stderr


def test_wrapper_permite_execucao_com_data_e_execution_id(framework):
    result = run_wrapper(
        framework,
        f"{framework}/main.sh --process-file {contract(framework)} "
        "--dates-pattern-files 20260909 --execution-id 7f3c1a90-0000-4000-8000-000000000001 --no-mail",
    )
    # o step 3 tem dev_fail com stop_on_failed=false: job segue e termina com 1
    assert result.returncode == 1, result.stderr


# --- wrapper: tudo o que precisa ser recusado -------------------------------

@pytest.mark.parametrize(
    "descricao,comando",
    [
        ("sessao interativa", ""),
        ("comando arbitrario", "id"),
        ("encadeamento", "{main} --process-file {c}; id"),
        ("pipe", "{main} --process-file {c} | tee /tmp/x"),
        ("substituicao de comando", "{main} --process-file $(ls)"),
        ("redirecionamento", "{main} --process-file {c} > /tmp/x"),
        ("glob", "{main} --process-file /tmp/*.json"),
        ("executavel nao permitido", "/bin/bash --process-file {c}"),
        ("contrato fora de processes", "{main} --process-file /etc/passwd"),
        ("travessia de diretorio", "{main} --process-file {proc}/../../../etc/shadow"),
        ("contrato inexistente", "{main} --process-file {proc}/reportes/nao_existe.json"),
        ("flag desconhecida", "{main} --process-file {c} --exec-shell"),
        ("manual-steps invalido", "{main} --process-file {c} --manual-steps 1;rm"),
        ("data invalida", "{main} --process-file {c} --dates-pattern-files ontem"),
        ("execution-id invalido", "{main} --process-file {c} --execution-id 'nao valido'"),
        ("sem process-file", "{main} --no-mail --validate-file"),
    ],
)
def test_wrapper_recusa(framework, descricao, comando):
    formatted = comando.format(
        main=f"{framework}/main.sh", c=contract(framework), proc=f"{framework}/processes"
    )
    result = run_wrapper(framework, formatted)
    assert result.returncode == 42, f"{descricao}: deveria recusar, saiu {result.returncode}"


# --- main.sh: semântica que a API expõe -------------------------------------

def test_validate_file_nao_executa_steps(framework):
    result = run_main(framework, "--process-file", contract(framework), "--validate-file")
    assert result.returncode == 0
    assert "validacao ok" in result.stdout
    assert "msg=\"step\"" not in result.stdout


def test_json_invalido_falha_na_validacao(framework):
    result = run_main(framework, "--process-file", contract(framework, "invalido.json"), "--validate-file")
    assert result.returncode == 4


def test_placeholders_de_data_resolvidos(framework):
    result = run_main(framework, "--process-file", contract(framework),
                      "--dates-pattern-files", "20260215", "--manual-steps", "2", "--no-mail")
    assert "r_2026_046.csv" in result.stdout   # 15/02 = dia juliano 046


def test_manual_steps_limita_execucao(framework):
    result = run_main(framework, "--process-file", contract(framework),
                      "--manual-steps", "1", "--no-mail")
    assert 'step=1 type=download' in result.stdout
    assert 'step=2 type=copy_local' not in result.stdout


def test_multiplas_datas_sao_serializadas(framework):
    result = run_main(framework, "--process-file", contract(framework),
                      "--dates-pattern-files", "20260101,20260102", "--manual-steps", "1", "--no-mail")
    inicios = [l for l in result.stdout.splitlines() if 'msg="inicio"' in l]
    assert len(inicios) == 2
    assert "target_date=20260101" in inicios[0]
    assert "target_date=20260102" in inicios[1]


def test_stop_on_failed_interrompe_o_job(framework, tmp_path):
    parada = {
        "schema_version": "1.0",
        "steps": [
            {"type": "download", "stop_on_failed": True, "dev_fail": True},
            {"type": "upload_remote", "stop_on_failed": True},
        ],
    }
    path = framework / "processes" / "reportes" / "parada.json"
    path.write_text(json.dumps(parada), encoding="utf-8")
    result = run_main(framework, "--process-file", str(path), "--no-mail")
    assert result.returncode == 1
    assert "interrompido por stop_on_failed" in result.stdout
    assert "step=2" not in result.stdout


def test_main_sh_nao_conhece_execution_id(framework):
    """O `main.sh` REAL trata flag desconhecida como fatal ("Opcion desconocida"
    -> main_help -> exit). O stub precisa ser igualmente restritivo: um stub
    mais permissivo que o original faria a suíte passar aqui e TODA execução
    falhar em UAT."""
    result = run_main(framework, "--process-file", contract(framework),
                      "--execution-id", "7f3c1a90-dead-4000-8000-000000000009", "--no-mail")
    assert result.returncode == 2
    assert "flag desconhecida" in result.stderr


def test_execution_id_vai_no_nome_do_log(framework):
    """A correlação nasce no wrapper, não no legado: ele controla o
    redirecionamento e nomeia o arquivo por `execution_id` sem que o `main.sh`
    precise saber que o identificador existe."""
    run_wrapper(
        framework,
        f"{framework}/main.sh --process-file {contract(framework)} "
        "--execution-id 7f3c1a90-dead-4000-8000-000000000009 --manual-steps 1 --no-mail",
    )
    logs = list((framework / "logs").rglob("*.log"))
    assert any("7f3c1a90-dead" in p.name for p in logs), [p.name for p in logs]


def test_wrapper_nao_repassa_execution_id_ao_legado(framework):
    """Se repassasse, o main.sh real abortaria antes de executar qualquer step."""
    run_wrapper(
        framework,
        f"{framework}/main.sh --process-file {contract(framework)} "
        "--execution-id 7f3c1a90-dead-4000-8000-000000000456 --manual-steps 1 --no-mail",
    )
    bo_log = next((p for p in (framework / "logs").rglob("*000000000456*.log")), None)
    assert bo_log is not None, "wrapper nao gravou o log correlacionado"
    # O job rodou de verdade: o stub registra o step no proprio log.
    assert "step" in bo_log.read_text().lower() or "execution_id" in bo_log.read_text()
    assert "flag desconhecida" not in bo_log.read_text()
