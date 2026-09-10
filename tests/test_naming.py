"""Convenção de nomes -> dimensões.

Os casos vêm de padrões reais observados no inventário de 09/2026, com códigos
de cliente substituídos por códigos fictícios (`aaa`, `bbb`) para não versionar
nome de cliente no repositório.
"""

from __future__ import annotations

import pytest

from catalog.naming import Vocabulary, parse_process_name

VOCAB_YAML = """
environments: {prd: PROD, prod: PROD, pro: PROD, uat: UAT, tst: TEST}
domains:
  base2: [bs2]
  emboces: [ebc]
  emisiones: [emi]
  transacciones: [txn, dtx]
  reportes: [rpt]
  saldos: [sal]
  otros: [otr]
countries: {col: CO, pan: PA, dom: DO, per: PE, peru: PE, slv: SV, cri: CR, int: INTL}
clients: {aaa: "Cliente A"}
variant_suffixes: [v2, new, manual, masivo]
"""


@pytest.fixture()
def vocab(tmp_path):
    path = tmp_path / "vocabulary.yaml"
    path.write_text(VOCAB_YAML, encoding="utf-8")
    return Vocabulary.load(path)


def test_caso_simples(vocab):
    parsed = parse_process_name("prd_aaa_col_bs2.json", vocab, domain_dir="base2")
    assert parsed.environment == "PROD"
    assert parsed.client_code == "aaa"
    assert parsed.client_name == "Cliente A"     # curado no vocabulário
    assert parsed.country_codes == ("CO",)
    assert parsed.domain_from_name == "base2"
    assert parsed.process_suffix is None
    assert parsed.flags == ()


def test_cliente_composto(vocab):
    # padrão real de co-branding: dois códigos de cliente antes do país
    parsed = parse_process_name("prd_bbb_aaa_col_ebc.sh", vocab, domain_dir="emboces")
    assert parsed.client_code == "bbb_aaa"
    assert parsed.country_codes == ("CO",)
    assert parsed.domain_from_name == "emboces"


def test_multiplos_paises(vocab):
    parsed = parse_process_name("prd_aaa_slv_cri_bs2_manual.json", vocab, domain_dir="base2")
    assert parsed.client_code == "aaa"
    assert parsed.country_codes == ("SV", "CR")
    assert parsed.process_suffix == "manual"
    assert "sufixo-de-variante" in parsed.flags


def test_cliente_e_pais_com_mesmo_codigo(vocab):
    # 'per' é código de cliente e de país: sobra sempre um token para o cliente
    parsed = parse_process_name("prd_per_per_ebc_soles.json", vocab, domain_dir="emboces")
    assert parsed.client_code == "per"
    assert parsed.country_codes == ("PE",)
    assert parsed.process_suffix == "soles"


def test_grafias_alternativas_de_producao(vocab):
    for token in ("prd", "prod", "pro"):
        parsed = parse_process_name(f"{token}_aaa_dom_rpt.json", vocab, domain_dir="reportes")
        assert parsed.environment == "PROD", token
        assert parsed.environment_token == token


def test_ambiente_uat_em_host_de_producao(vocab):
    parsed = parse_process_name("uat_aaa_peru_bs2_valida.json", vocab, domain_dir="base2")
    assert parsed.environment == "UAT"
    assert parsed.country_codes == ("PE",)


def test_data_no_nome_e_sinalizada(vocab):
    parsed = parse_process_name("prd_aaa_int_txn_19022025.json", vocab, domain_dir="transacciones")
    assert parsed.country_codes == ("INTL",)
    assert "data-no-nome" in parsed.flags


def test_dominio_divergente_do_diretorio(vocab):
    parsed = parse_process_name("prd_aaa_col_bs2.json", vocab, domain_dir="reportes")
    assert any(f.startswith("dominio-divergente") for f in parsed.flags)


def test_cliente_nao_curado(vocab):
    parsed = parse_process_name("prd_zzz_col_rpt.json", vocab, domain_dir="reportes")
    assert parsed.client_code == "zzz"
    assert parsed.client_name is None
    assert "cliente-nao-curado" in parsed.flags


def test_nome_fora_da_convencao(vocab):
    parsed = parse_process_name("example_process.json", vocab)
    assert parsed.environment is None
    assert "ambiente-nao-identificado" in parsed.flags
    assert "dominio-nao-identificado-no-nome" in parsed.flags
    assert not parsed.is_fully_resolved


# -- país logo após o cliente, com o resto do nome sendo descrição do processo
# (padrão dominante nos nomes sem abreviação de domínio) ----------------------

def test_pais_no_meio_sem_abreviacao_de_dominio(vocab):
    parsed = parse_process_name("prd_aaa_dom_extraer_apertura_gaveta.sh", vocab, domain_dir="otros")
    assert parsed.client_code == "aaa"
    assert parsed.country_codes == ("DO",)
    assert parsed.process_suffix == "extraer_apertura_gaveta"
    assert "dominio-nao-identificado-no-nome" in parsed.flags


def test_pais_no_meio_com_abreviacao_depois(vocab):
    # prd_<cliente>_<pais>_<descricao>_<abrev>: o sufixo junta o que sobra
    parsed = parse_process_name("prd_aaa_dom_pre_txn_manual.sh", vocab, domain_dir="transacciones")
    assert parsed.client_code == "aaa"
    assert parsed.country_codes == ("DO",)
    assert parsed.domain_from_name == "transacciones"
    assert parsed.process_suffix == "pre_manual"


def test_cobranding_com_pais_no_meio(vocab):
    parsed = parse_process_name("prd_bbb_aaa_per_terceiro_ebc_envio.sh", vocab, domain_dir="emboces")
    assert parsed.client_code == "bbb_aaa"
    assert parsed.country_codes == ("PE",)
    assert parsed.process_suffix == "terceiro_envio"


def test_regra_do_fim_tem_precedencia(vocab):
    # com país no fim do trecho do meio, nada muda: cliente composto preservado
    parsed = parse_process_name("prd_bbb_aaa_col_ebc.sh", vocab, domain_dir="emboces")
    assert parsed.client_code == "bbb_aaa"
    assert parsed.country_codes == ("CO",)


def test_nome_sem_pais_permanece_sem_pais(vocab):
    parsed = parse_process_name("prd_aaa_bbb_ebc_env.sh", vocab, domain_dir="emboces")
    assert parsed.client_code == "aaa_bbb"
    assert parsed.country_codes == ()
    assert "pais-nao-identificado" in parsed.flags
