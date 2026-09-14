"""Validação de schema do contrato (SPEC requisito 3).

A regra que governa estes testes vem do invariante 1: o contrato JSON é
interface estável. Um schema que reprovasse contrato que roda em produção hoje
seria um schema errado — então o teste de baseline roda contra os 575 contratos
reais da coleta e exige que o parque passe.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from catalog.contract_schema import (
    STEP_FUNCTIONS,
    ContractInvalid,
    Policy,
    Severity,
    assert_valid,
    validate_contract,
)

REPO = Path(__file__).resolve().parents[1]
PARQUE = REPO / "seed/raw/batch-seed-srv-sftp-2-20260910T110950Z/framework/processes"

CONTRATO_VALIDO = {
    "name_process": "base2",
    "client": "alianza_del_valle",
    "country": "ecuador",
    "environment": "prd",
    "description": "Proceso de base2",
    "send_infra_mail": "BOTH",
    "steps": [
        {"step": 1, "function": "download", "stop_on_failed": True, "server": "novo_37_90",
         "files": [{"file_name": "arq_@@@YYYYMMDD@@@.txt", "remote_path": "/out",
                    "local_path": ".", "remove_on_source": False, "min_lines": 1}]},
        {"step": 2, "function": "upload_remote", "stop_on_failed": False,
         "server_remote": "cliente_externo_1",
         "files": [{"file_name": "saida.txt", "source": "saida.txt",
                    "remote_path": "/in", "local_path": "."}]},
    ],
}


def _sem(campo):
    contrato = json.loads(json.dumps(CONTRATO_VALIDO))
    contrato.pop(campo)
    return contrato


def test_contrato_real_e_valido():
    assert validate_contract(CONTRATO_VALIDO).valid


def test_ausencia_de_schema_version_nao_invalida():
    """Nenhum dos 575 contratos tem o campo. Ausência é legado, nunca erro."""
    assert "schema_version" not in CONTRATO_VALIDO
    report = validate_contract(CONTRATO_VALIDO)
    assert report.valid
    assert report.status == "valid"


@pytest.mark.parametrize("campo", ["name_process", "client", "country", "environment", "steps"])
def test_campo_obrigatorio_ausente_e_erro(campo):
    report = validate_contract(_sem(campo))
    assert not report.valid
    assert any(v.rule == "required" for v in report.errors)


def test_funcao_desconhecida_e_erro():
    """O executor não sabe despachar função fora da lista — é erro, não extensão."""
    contrato = json.loads(json.dumps(CONTRATO_VALIDO))
    contrato["steps"][0]["function"] = "teleportar"
    report = validate_contract(contrato)
    assert not report.valid
    assert any("steps[0].function" == v.path for v in report.errors)


@pytest.mark.parametrize("funcao,campo", [
    ("download", "server"),
    ("upload_remote", "server_remote"),
    ("encrypt", "key"),
    ("execute_command", "command"),
    ("send_mail", "mails"),
])
def test_campo_exigido_pela_funcao(funcao, campo):
    """Cada função tem o campo sem o qual ela não executa."""
    assert campo in STEP_FUNCTIONS[funcao]
    step = {"step": 1, "function": funcao, "stop_on_failed": True}
    for obrigatorio in STEP_FUNCTIONS[funcao]:
        if obrigatorio == campo:
            continue
        step[obrigatorio] = [] if obrigatorio == "files" else "x"
    contrato = {**CONTRATO_VALIDO, "steps": [step]}
    report = validate_contract(contrato)
    assert not report.valid
    assert any(v.rule == "required" and campo in v.message for v in report.errors)


def test_arquivo_sem_nome_e_erro():
    contrato = json.loads(json.dumps(CONTRATO_VALIDO))
    contrato["steps"][0]["files"] = [{"remote_path": "/out"}]
    assert not validate_contract(contrato).valid


def test_tipo_errado_e_erro():
    contrato = json.loads(json.dumps(CONTRATO_VALIDO))
    contrato["steps"][0]["stop_on_failed"] = "true"   # string, não booleano
    assert not validate_contract(contrato).valid


def test_campo_desconhecido_e_aviso_nao_erro():
    """Extensão aditiva não pode ser recusada antes de existir (invariante 1)."""
    contrato = {**CONTRATO_VALIDO, "sla_minutos": 30}
    report = validate_contract(contrato)
    assert report.valid
    assert report.status == "valid_with_warnings"
    assert [v.severity for v in report.warnings] == [Severity.WARNING]


def test_numeracao_de_step_fora_de_ordem_e_aviso():
    contrato = json.loads(json.dumps(CONTRATO_VALIDO))
    contrato["steps"][1]["step"] = 7
    report = validate_contract(contrato)
    assert report.valid
    assert any(v.rule == "numeracao-nao-sequencial" for v in report.warnings)


def test_raiz_que_nao_e_objeto():
    assert not validate_contract([1, 2, 3]).valid


# --- políticas ---------------------------------------------------------------

def test_strict_recusa_contrato_invalido():
    with pytest.raises(ContractInvalid):
        assert_valid(_sem("steps"), Policy.STRICT)


def test_legacy_registra_e_nao_bloqueia():
    """Import do parque tem de conseguir registrar o que está quebrado."""
    report = assert_valid(_sem("steps"), Policy.LEGACY)
    assert not report.valid
    assert report.status == "invalid"


# --- baseline contra o parque real -------------------------------------------

@pytest.mark.skipif(not PARQUE.is_dir(), reason="coleta de 09/2026 ausente")
def test_parque_real_passa_no_schema():
    """574 dos 575 contratos passam. O único reprovado é defeito de verdade:
    `uat_teb_peru_bs2_valida.json` traz `commands` (lista) num step
    `execute_command`, que lê `command` (string) — step que não executa."""
    reprovados = {}
    for path in sorted(PARQUE.rglob("*.json")):
        report = validate_contract(json.loads(path.read_text(encoding="utf-8", errors="replace")))
        if not report.valid:
            reprovados[path.name] = [v.message for v in report.errors]

    assert set(reprovados) == {"uat_teb_peru_bs2_valida.json"}, reprovados
