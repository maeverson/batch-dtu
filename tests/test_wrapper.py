"""Parser dos wrappers de scheduler — o elo cron -> contrato."""

from __future__ import annotations

from catalog.wrapper import parse_wrapper

FW = "/opt2/batch_v2/batch-commons-framework"


def test_invocacao_simples():
    script = parse_wrapper(
        f"{FW}/schedulers/base2/prd_aaa_col_bs2.sh",
        f"#!/bin/bash\nbash {FW}/main.sh --process-file {FW}/processes/base2/prd_aaa_col_bs2.json --no-mail\n",
    )
    assert len(script.invocations) == 1
    inv = script.invocations[0]
    assert inv.process_file == f"{FW}/processes/base2/prd_aaa_col_bs2.json"
    assert inv.no_mail is True
    assert inv.manual_steps is None
    assert inv.process_file_is_resolved


def test_todas_as_flags_de_operacao():
    script = parse_wrapper(
        "w.sh",
        f"{FW}/main.sh --process-file {FW}/processes/otros/prd_aaa_col_otr.json "
        "--manual-steps 3,4 --dates-pattern-files 20260909 --no-mail\n",
    )
    inv = script.invocations[0]
    assert inv.manual_steps == "3,4"
    assert inv.dates_pattern == "20260909"
    assert inv.no_mail is True


def test_flag_com_igual():
    script = parse_wrapper("w.sh", f"{FW}/main.sh --process-file={FW}/processes/otros/x.json\n")
    assert script.invocations[0].process_file == f"{FW}/processes/otros/x.json"


def test_caminho_entre_aspas_com_espaco():
    # patologia real: diretório com espaço no fim do nome
    script = parse_wrapper(
        "w.sh", f'{FW}/main.sh --process-file "{FW}/processes/otros/dir com espaco/x.json"\n'
    )
    assert script.invocations[0].process_file == f"{FW}/processes/otros/dir com espaco/x.json"


def test_variavel_do_proprio_wrapper_e_expandida():
    script = parse_wrapper(
        "w.sh",
        f'BASE={FW}\nPROC="$BASE/processes"\n$BASE/main.sh --process-file $PROC/otros/x.json\n',
    )
    inv = script.invocations[0]
    assert inv.process_file == f"{FW}/processes/otros/x.json"
    assert inv.unresolved == ()


def test_variavel_externa_fica_marcada_como_nao_resolvida():
    script = parse_wrapper("w.sh", "$FW/main.sh --process-file $UNKNOWN/otros/x.json\n")
    inv = script.invocations[0]
    assert inv.unresolved
    assert not inv.process_file_is_resolved


def test_multiplas_invocacoes_no_mesmo_wrapper():
    script = parse_wrapper(
        "w.sh",
        f"{FW}/main.sh --process-file {FW}/processes/otros/a.json\n"
        f"{FW}/main.sh --process-file {FW}/processes/otros/b.json --manual-steps 2\n",
    )
    assert len(script.invocations) == 2
    assert any(f.startswith("multiplas-invocacoes") for f in script.flags)
    assert script.process_files == (
        f"{FW}/processes/otros/a.json",
        f"{FW}/processes/otros/b.json",
    )


def test_linhas_comentadas_sao_ignoradas():
    script = parse_wrapper("w.sh", f"# {FW}/main.sh --process-file {FW}/processes/otros/velho.json\n")
    assert script.invocations == ()
    assert "sem-invocacao-de-main-sh" in script.flags


def test_path_truncado_e_preservado_como_esta():
    # bug real: falta 's/otros/' no caminho; o parser não conserta, só reporta
    script = parse_wrapper("w.sh", f"bash {FW}/main.sh --process-file {FW}/processeprd_aaa_col_otr.json\n")
    assert script.invocations[0].process_file == f"{FW}/processeprd_aaa_col_otr.json"
