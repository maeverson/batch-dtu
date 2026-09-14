"""Parser de crontab — o que não pode ser perdido do legado."""

from __future__ import annotations

from catalog.cron import EntryKind, parse_crontab

FW = "/opt2/batch_v2/batch-commons-framework"

CRONTAB = f"""\
CRON_TZ=America/Lima
PATH=/sbin:/bin:/usr/sbin:/usr/bin

# Relatorio diario - owner: equipe DTU
00 02 * * * {FW}/schedulers/reportes/prd_aaa_col_rpt.sh >> {FW}/logs/schedulers/reportes/prd_aaa_col_rpt.log

# desabilitado em 2025-11 a pedido do cliente
#30 09 * * * {FW}/schedulers/base2/prd_aaa_col_bs2.sh >> {FW}/logs/schedulers/base2/prd_aaa_col_bs2.log
*/15 * * * * /home/batch_user/scripts/clean_empty_folders.sh
0 0 * * *  crontab -l | gzip > /home/batch_user/backups/crontab.gz
00 03 * * * {FW}/schedulers/otros/prd_bbb_pan_otr.sh ##Migrado a batch_user
@daily {FW}/schedulers/saldos/prd_aaa_pan_sal.sh
isso nao e uma linha de cron valida
"""


def test_total_de_linhas_e_preservado():
    entries = parse_crontab(CRONTAB, source="user-batch_user")
    assert len(entries) == len(CRONTAB.splitlines())


def test_atribuicoes_de_ambiente():
    entries = parse_crontab(CRONTAB)
    envs = {e.env_name: e.env_value for e in entries if e.kind is EntryKind.ENV}
    assert envs["CRON_TZ"] == "America/Lima"
    assert "PATH" in envs


def test_job_ativo_captura_agenda_script_e_log():
    entries = parse_crontab(CRONTAB)
    job = next(e for e in entries if e.kind is EntryKind.JOB and e.enabled)
    assert job.schedule == "00 02 * * *"
    assert job.script_path.endswith("/schedulers/reportes/prd_aaa_col_rpt.sh")
    assert job.log_path.endswith("prd_aaa_col_rpt.log")
    assert job.status_reason == "Relatorio diario - owner: equipe DTU"


def test_linha_comentada_com_comando_e_job_desabilitado_com_motivo():
    entries = parse_crontab(CRONTAB)
    disabled = [e for e in entries if e.kind is EntryKind.JOB and not e.enabled]
    assert len(disabled) == 1
    assert disabled[0].schedule == "30 09 * * *"
    assert disabled[0].status_reason == "desabilitado em 2025-11 a pedido do cliente"


def test_comentario_de_prosa_nao_vira_job():
    entries = parse_crontab(CRONTAB)
    comments = [e for e in entries if e.kind is EntryKind.COMMENT]
    assert any("owner: equipe DTU" in (c.inline_comment or "") for c in comments)


def test_script_fora_de_schedulers_e_manutencao():
    entries = parse_crontab(CRONTAB)
    maintenance = [e for e in entries if e.kind is EntryKind.MAINTENANCE]
    assert any("clean_empty_folders.sh" in (m.script_path or "") for m in maintenance)
    # comando sem .sh (pipeline com crontab -l) também é agendável, não some
    assert any(m.script_path is None for m in maintenance)


def test_comentario_inline_e_capturado():
    entries = parse_crontab(CRONTAB)
    job = next(e for e in entries if e.script_path and e.script_path.endswith("prd_bbb_pan_otr.sh"))
    assert job.inline_comment == "Migrado a batch_user"
    assert "##" not in job.command


def test_macro_de_agenda():
    entries = parse_crontab(CRONTAB)
    job = next(e for e in entries if e.schedule == "@daily")
    assert job.kind is EntryKind.JOB
    assert job.script_path.endswith("prd_aaa_pan_sal.sh")


def test_linha_invalida_nao_e_descartada():
    entries = parse_crontab(CRONTAB)
    unparsed = [e for e in entries if e.kind is EntryKind.UNPARSED]
    assert len(unparsed) == 1
    assert unparsed[0].reason


def test_crontab_de_sistema_com_campo_de_usuario():
    entries = parse_crontab("0 */1 * * * root /usr/local/bin/agent\n", source="/etc/crontab")
    entry = entries[0]
    assert entry.schedule == "0 */1 * * *"
    assert entry.command == "/usr/local/bin/agent"
    assert "run_as:root" in entry.flags


def test_cabecalho_de_procedencia_e_ignorado():
    entries = parse_crontab("### source=/var/spool/cron/batch_user\n0 1 * * * /x/y.sh\n")
    assert len(entries) == 1
    assert entries[0].kind is EntryKind.MAINTENANCE


# -- padrões reais de desabilitação com motivo fundido na linha ---------------

def test_motivo_e_ticket_fundidos_antes_da_agenda():
    line = f"#### DESINCORPORADO #### CDSI-162 00 09 * * * {FW}/schedulers/base2/prd_aaa_pan_bs2.sh >> /var/log/x.log"
    entry = parse_crontab(line + "\n")[0]
    assert entry.kind is EntryKind.JOB
    assert entry.enabled is False
    assert entry.schedule == "00 09 * * *"
    assert entry.script_path.endswith("prd_aaa_pan_bs2.sh")
    assert entry.status_reason == "DESINCORPORADO CDSI-162"


def test_ticket_colado_no_primeiro_campo_da_agenda():
    line = f"###NP-16498####20 03 * * *  {FW}/schedulers/saldos/prd_aaa_slv_sal.sh >> /var/log/y.log"
    entry = parse_crontab(line + "\n")[0]
    assert entry.kind is EntryKind.JOB
    assert entry.enabled is False
    assert entry.schedule == "20 03 * * *"
    assert entry.status_reason == "NP-16498"


def test_prosa_pura_continua_comentario():
    entry = parse_crontab("# desabilitado em 2025-11 a pedido do cliente\n")[0]
    assert entry.kind is EntryKind.COMMENT


def test_prosa_acima_soma_com_motivo_da_propria_linha():
    text = (
        "# pedido do cliente via ticket\n"
        f"#### DESINCORPORADO #### 00 09 * * * {FW}/schedulers/otros/prd_aaa_col_otr.sh\n"
    )
    entries = parse_crontab(text)
    job = next(e for e in entries if e.kind is EntryKind.JOB)
    assert job.status_reason == "pedido do cliente via ticket\nDESINCORPORADO"


def test_wrapper_comentado_sem_agenda_e_job_on_demand():
    line = f"#{FW}/schedulers/otros/prd_aaa_dom_otr_full_extract.sh"
    entry = parse_crontab(line + "\n")[0]
    assert entry.kind is EntryKind.JOB
    assert entry.enabled is False
    assert entry.schedule is None          # não se inventa agenda
    assert "on-demand" in entry.flags
    assert entry.script_path.endswith("prd_aaa_dom_otr_full_extract.sh")


def test_on_demand_com_log_e_motivo():
    line = f"## reprocesso manual {FW}/schedulers/otros/prd_aaa_dom_otr_x.sh >> /var/log/x.log"
    entry = parse_crontab(line + "\n")[0]
    assert entry.kind is EntryKind.JOB
    assert entry.schedule is None
    assert entry.status_reason == "reprocesso manual"
    assert entry.log_path == "/var/log/x.log"


def test_comentario_com_script_fora_de_schedulers_nao_vira_job():
    entry = parse_crontab("#/home/batch_user/scripts/clean_empty_folders.sh\n")[0]
    assert entry.kind is EntryKind.COMMENT


# --- marcador do Back Office -------------------------------------------------

JOB_ID = "6b0a1f2e-6a1e-4a3e-9d2f-0c8b7a5e4d31"
CHANGE_ID = "f1e2d3c4-b5a6-4978-8a9b-0c1d2e3f4a5b"


def test_marcador_bo_liga_linha_ao_change_request():
    """A âncora que a reconciliação usa para fechar o loop.

    Sem ela, `catálogo diz desabilitado / crontab diz ativo` é indistinguível de
    alguém editando o crontab por fora — e os dois casos exigem resposta oposta.
    """
    texto = (
        f"#BO:{JOB_ID}:{CHANGE_ID} desabilitado a pedido do cliente\n"
        "#00 02 * * * /fw/schedulers/otros/prd_aaa_col_otr.sh >> /fw/logs/x.log\n"
    )
    entradas = parse_crontab(texto, source="user-batch")
    agendada = [e for e in entradas if e.is_schedulable]
    assert len(agendada) == 1
    assert agendada[0].enabled is False
    assert agendada[0].bo_job_id == JOB_ID
    assert agendada[0].bo_change_id == CHANGE_ID


def test_marcador_vale_para_linha_ativa():
    texto = (
        f"#BO:{JOB_ID}:{CHANGE_ID} reativado apos janela\n"
        "00 02 * * * /fw/schedulers/otros/prd_aaa_col_otr.sh >> /fw/logs/x.log\n"
    )
    agendada = [e for e in parse_crontab(texto) if e.is_schedulable]
    assert agendada[0].enabled is True
    assert agendada[0].bo_change_id == CHANGE_ID


def test_motivo_do_marcador_entra_como_razao_mas_o_marcador_nao():
    """O motivo canônico vive no catálogo; o crontab guarda a âncora."""
    texto = (
        f"#BO:{JOB_ID}:{CHANGE_ID} cliente suspendeu o contrato\n"
        "#00 02 * * * /fw/schedulers/otros/prd_aaa_col_otr.sh\n"
    )
    agendada = [e for e in parse_crontab(texto) if e.is_schedulable][0]
    assert "cliente suspendeu o contrato" in (agendada.status_reason or "")
    assert "BO:" not in (agendada.status_reason or "")


def test_linha_sem_marcador_nao_inventa_change_id():
    texto = "00 02 * * * /fw/schedulers/otros/prd_aaa_col_otr.sh\n"
    agendada = [e for e in parse_crontab(texto) if e.is_schedulable][0]
    assert agendada.bo_job_id is None and agendada.bo_change_id is None


def test_prosa_livre_parecida_com_marcador_nao_e_marcador():
    """`# BO vai desabilitar isso` é prosa, não âncora."""
    texto = ("# BO vai desabilitar isso semana que vem\n"
             "00 02 * * * /fw/schedulers/otros/prd_aaa_col_otr.sh\n")
    agendada = [e for e in parse_crontab(texto) if e.is_schedulable][0]
    assert agendada.bo_change_id is None
    assert "BO vai desabilitar" in (agendada.status_reason or "")
