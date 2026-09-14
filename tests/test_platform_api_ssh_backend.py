"""`build_invocation` é o núcleo de segurança da Platform API: a linha que
sai daqui vira `SSH_ORIGINAL_COMMAND` no host legado. Testado sem SSH, sem
container — a garantia não pode depender de infraestrutura estar de pé.

Espelha as recusas de `docker/legacy/batch-wrapper.sh`: o que passa aqui tem
de ser exatamente o que o wrapper aceitaria, e vice-versa.
"""

from __future__ import annotations

import re

import pytest

from catalog.db.models import Job
from platform_api.ssh_backend import (
    ExecutionRequest,
    InMemoryExecutionBackend,
    InvalidExecutionRequest,
    build_invocation,
)

FW = "/opt2/batch_v2/batch-commons-framework"


def _job(**overrides) -> Job:
    base = dict(
        host="srv-sftp-2", process_name="prd_aaa_col_rpt",
        contract_path=f"{FW}/processes/reportes/prd_aaa_col_rpt.json",
        domain="reportes", environment="PROD", status="active",
    )
    base.update(overrides)
    return Job(**base)


def test_invocacao_minima():
    linha = build_invocation(ExecutionRequest(job=_job()), execution_id="a" * 8)
    assert linha == (
        f"main.sh --process-file {FW}/processes/reportes/prd_aaa_col_rpt.json "
        "--execution-id aaaaaaaa"
    )


def test_gera_execution_id_quando_omitido():
    linha = build_invocation(ExecutionRequest(job=_job()))
    m = re.search(r"--execution-id ([0-9a-fA-F-]+)$", linha)
    assert m and len(m.group(1)) >= 8


def test_todos_os_campos():
    req = ExecutionRequest(
        job=_job(), steps="3,4-6", dates_pattern="20260901,20260902",
        no_mail=True, validate_only=True,
    )
    linha = build_invocation(req, execution_id="b" * 8)
    assert linha == (
        f"main.sh --process-file {FW}/processes/reportes/prd_aaa_col_rpt.json "
        "--manual-steps 3,4-6 --dates-pattern-files 20260901,20260902 "
        "--no-mail --validate-file --execution-id bbbbbbbb"
    )


def test_datas_iso_tambem_aceitas():
    req = ExecutionRequest(job=_job(), dates_pattern="2026-09-01,2026-09-02")
    linha = build_invocation(req, execution_id="c" * 8)
    assert "--dates-pattern-files 2026-09-01,2026-09-02" in linha


# --- o mesmo denylist do wrapper ---------------------------------------------

METACARACTERES = [";", "|", "&", "$", "`", ">", "<", "(", ")", "{", "}", "\n", "*", "?", "~", "\\"]


@pytest.mark.parametrize("meta", METACARACTERES)
def test_metacaractere_em_steps_e_recusado(meta):
    req = ExecutionRequest(job=_job(), steps=f"1{meta}2")
    with pytest.raises(InvalidExecutionRequest):
        build_invocation(req)


@pytest.mark.parametrize("valor", [
    "3; rm -rf /", "3 && cat /etc/passwd", "$(whoami)", "`id`", "3|4",
    "../../etc/passwd", "3\n--no-mail",
])
def test_steps_com_tentativa_de_injecao_e_recusado(valor):
    with pytest.raises(InvalidExecutionRequest):
        build_invocation(ExecutionRequest(job=_job(), steps=valor))


@pytest.mark.parametrize("valor", [
    "hoje", "20260901; rm -rf /", "20260901 --no-mail", "$(date +%Y%m%d)",
])
def test_dates_pattern_invalido_e_recusado(valor):
    with pytest.raises(InvalidExecutionRequest):
        build_invocation(ExecutionRequest(job=_job(), dates_pattern=valor))


def test_dates_pattern_so_valida_formato_como_o_wrapper():
    """`2026-13-01` passa: o wrapper real (`batch-wrapper.sh`) também só
    valida formato (regex `\\d{4}-\\d{2}-\\d{2}`), não calendário — mês 13 é
    pego depois, na resolução de placeholder do `main.sh`. Divergir disso
    faria a API recusar algo que o caminho SSH direto aceitaria."""
    linha = build_invocation(ExecutionRequest(job=_job(), dates_pattern="2026-13-01"))
    assert "--dates-pattern-files 2026-13-01" in linha


def test_execution_id_com_metacaractere_e_recusado():
    with pytest.raises(InvalidExecutionRequest):
        build_invocation(ExecutionRequest(job=_job()), execution_id="a;rm -rf /")


def test_contrato_fora_de_processes_e_recusado():
    job = _job(contract_path="/tmp/evil.json")
    with pytest.raises(InvalidExecutionRequest):
        build_invocation(ExecutionRequest(job=job))


def test_travessia_de_diretorio_no_contrato_e_recusada():
    job = _job(contract_path=f"{FW}/processes/../../../etc/passwd")
    with pytest.raises(InvalidExecutionRequest):
        build_invocation(ExecutionRequest(job=job))


def test_job_sem_contrato_e_recusado():
    job = _job(contract_path=None)
    with pytest.raises(InvalidExecutionRequest):
        build_invocation(ExecutionRequest(job=job))


def test_upload_remote_nao_muda_a_invocacao():
    """O contrato pode ter step upload_remote — quem decide se isso exige
    confirmação reforçada é a camada de API (routers/executions.py), não o
    backend. O backend só monta a invocação do processo, sempre igual."""
    job = _job(process_name="prd_bbb_col_ebc")
    linha_normal = build_invocation(ExecutionRequest(job=job), execution_id="d" * 8)
    assert "upload_remote" not in linha_normal   # não é campo da invocação


# --- backend em memória: mesma validação do backend real ---------------------

async def _run(coro):
    return await coro


def test_backend_em_memoria_valida_como_o_real():
    import asyncio

    backend = InMemoryExecutionBackend()
    with pytest.raises(InvalidExecutionRequest):
        asyncio.run(backend.dispatch(
            ExecutionRequest(job=_job(), steps="1;2"), execution_id="e" * 8
        ))
    assert backend.calls == []


def test_backend_em_memoria_registra_a_chamada():
    import asyncio

    backend = InMemoryExecutionBackend()
    resultado = asyncio.run(backend.dispatch(
        ExecutionRequest(job=_job(), steps="1,2"), execution_id="f" * 8
    ))
    assert resultado.exit_code == 0
    assert resultado.result == "success"
    assert len(backend.calls) == 1
    assert backend.calls[0][1] == "f" * 8
