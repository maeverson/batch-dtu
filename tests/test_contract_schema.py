"""Schema real dos contratos JSON e as dimensões que ele declara.

Confirmado nos 574 contratos de 09/2026 — e diferente do que o doc de
arquitetura descrevia:

    topo : name_process, client, country, environment, description,
           send_infra_mail, steps[], additional_info
    step : step, function, stop_on_failed + campos por função
           (files, server, server_remote, command, key, arguments, mails…)

Dois pontos que estes testes travam:

* o alias de conexão está em `server`/`server_remote`, não em `connection`;
* `schema_version` **não existe** em nenhum contrato. O campo do invariante 1 é
  extensão futura; contrato sem ele é legado, nunca erro.
"""

from __future__ import annotations

import json

import pytest

from catalog.build import _detect_client_outliers, _inspect_contract, _normalize_countries
from catalog.naming import Vocabulary, parse_process_name

CONTRATO_REAL = {
    "name_process": "base2",
    "client": "alianza_del_valle",
    "country": "ecuador",
    "environment": "prd",
    "description": "Proceso de base2",
    "send_infra_mail": "BOTH",
    "steps": [
        {"step": 1, "function": "download", "stop_on_failed": True,
         "server": "novo_37_90", "files": "arq_@@@YYYYMMDD@@@.txt"},
        {"step": 2, "function": "copy_local", "stop_on_failed": True, "server": "local"},
        {"step": 3, "function": "upload_remote", "stop_on_failed": False,
         "server_remote": "cliente_externo_1", "files": "saida.txt"},
        {"step": 4, "function": "encrypt", "stop_on_failed": True, "key": "cliente.asc"},
    ],
}


@pytest.fixture()
def contrato(tmp_path):
    path = tmp_path / "prd_alv_ecu_bs2.json"
    path.write_text(json.dumps(CONTRATO_REAL), encoding="utf-8")
    return path


def test_le_funcoes_dos_steps(contrato):
    facts = _inspect_contract(contrato)
    assert facts.steps_count == 4
    assert facts.functions == ("download", "copy_local", "upload_remote", "encrypt")


def test_alias_vem_de_server_e_server_remote(contrato):
    facts = _inspect_contract(contrato)
    assert facts.aliases == ("novo_37_90", "cliente_externo_1")


def test_pseudo_alias_local_nao_e_conexao(contrato):
    # `local` aparece 309 vezes no parque; é operação local, não conexão
    assert "local" not in _inspect_contract(contrato).aliases


def test_dimensoes_declaradas_no_contrato(contrato):
    facts = _inspect_contract(contrato)
    assert facts.client == "alianza_del_valle"
    assert facts.country == "ecuador"
    assert facts.environment == "prd"
    assert facts.process == "base2"


def test_ausencia_de_schema_version_nao_e_erro(contrato):
    facts = _inspect_contract(contrato)
    assert facts.schema_version is None
    assert facts.error is None


def test_json_invalido_reporta_erro(tmp_path):
    path = tmp_path / "quebrado.json"
    path.write_text('{"steps": [', encoding="utf-8")
    facts = _inspect_contract(path)
    assert facts.error
    assert facts.steps_count is None


def test_raiz_que_nao_e_objeto_reporta_erro(tmp_path):
    path = tmp_path / "lista.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert _inspect_contract(path).error


# --- país declarado por extenso ---------------------------------------------

def test_pais_por_extenso_e_normalizado():
    vocab = Vocabulary.load()
    assert _normalize_countries("ecuador", vocab) == ("EC",)
    assert _normalize_countries("republica_dominicana", vocab) == ("DO",)


def test_pais_composto_no_contrato():
    vocab = Vocabulary.load()
    assert set(_normalize_countries("costa_rica_guatemala_salvador", vocab)) == {"CR", "GT", "SV"}


def test_ordem_de_pais_nao_e_divergencia():
    """`cri_slv_gtm` no nome e `costa_rica_guatemala_salvador` no contrato são
    o mesmo escopo — comparar por posição gerava falso-positivo."""
    vocab = Vocabulary.load()
    do_nome = set(parse_process_name("prd_ggt_cri_slv_gtm_txn.sh", vocab).country_codes)
    do_contrato = set(_normalize_countries("costa_rica_guatemala_salvador", vocab))
    assert do_nome == do_contrato


# --- outlier de cliente ------------------------------------------------------

def _record(process_name: str, code: str, contract_client: str):
    from catalog.build import JobRecord

    return JobRecord(
        process_name=process_name, domain="reportes", environment="PROD",
        client_code=code, client_name=contract_client, country_codes=("CO",),
        schedule="0 1 * * *", timezone="America/Lima", enabled=True, status_reason=None,
        wrapper_path=f"/fw/schedulers/reportes/{process_name}.sh",
        contract_path=f"/fw/processes/reportes/{process_name}.json",
        contract_hash="x", contract_bytes=1, schema_version=None, steps_count=1,
        manual_steps=None, dates_pattern=None, no_mail=False, log_path=None,
        cron_source="user-batch", cron_lineno=1, contract_client=contract_client,
    )


def test_outlier_de_cliente_e_erro():
    """1 job declarando outro cliente contra 110 do dominante: contrato errado."""
    jobs = [_record(f"prd_stb_col_rpt_{i}", "stb", "servitebca") for i in range(20)]
    jobs.append(_record("prd_stb_col_rpt_suspeito", "stb", "codesarrollo"))
    findings: list = []
    _detect_client_outliers(jobs, findings)
    outliers = [f for f in findings if f.kind == "cliente-outlier-no-contrato"]
    assert len(outliers) == 1
    assert "codesarrollo" in outliers[0].detail
    assert outliers[0].subject == "prd_stb_col_rpt_suspeito"


def test_variacao_ortografica_nao_e_outlier():
    jobs = [_record(f"prd_ban_dom_rpt_{i}", "ban", "banreservas") for i in range(20)]
    jobs.append(_record("prd_ban_dom_rpt_x", "ban", "banreserva"))   # sem o 's'
    findings: list = []
    _detect_client_outliers(jobs, findings)
    assert not findings


def test_codigo_ambiguo_e_aviso():
    """Dois clientes reais com o mesmo código de 3 letras não é erro de dado."""
    jobs = [_record(f"prd_coo_col_rpt_{i}", "coo", "coopcentral") for i in range(17)]
    jobs += [_record(f"prd_coo_ecu_rpt_{i}", "coo", "cooperativa_29") for i in range(8)]
    findings: list = []
    _detect_client_outliers(jobs, findings)
    kinds = {f.kind for f in findings}
    assert kinds == {"codigo-de-cliente-ambiguo"}
