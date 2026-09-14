"""Persistência do catálogo contra Postgres real.

Verifica os três controles que sustentam os critérios de aceite do SPEC:

* **imutabilidade** de `audit_event`, `job_contract_version` e `job_revision`
  — por privilégio (REVOKE) e por trigger, inclusive escrevendo direto na
  partição;
* **idempotência** do import — rodar duas vezes não duplica nem inventa
  evento de auditoria;
* **auditoria na mesma transação** — mudança e trilha comitam juntas.

Pula automaticamente se o Postgres do compose não estiver no ar.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from catalog.build import Severity, build_catalog
from catalog.db.models import (
    AuditEvent,
    ConnectionAlias,
    CrontabEntryRow,
    Job,
    JobContractVersion,
    JobRevision,
    JobSchedule,
    ReconciliationFinding,
)
from catalog.db.repository import load_catalog
from catalog.naming import Vocabulary
from catalog.seedpkg import SeedPackage

TEST_URL = os.environ.get(
    "DATABASE_URL_TEST",
    "postgresql+psycopg://batch_migrator:batch_migrator_dev@localhost:5432/batch_catalog_test",
)
REPO = Path(__file__).resolve().parents[1]


def _postgres_disponivel() -> bool:
    try:
        from sqlalchemy import create_engine

        engine = create_engine(TEST_URL, connect_args={"connect_timeout": 2})
        with engine.connect():
            return True
    except Exception:
        return False
    finally:
        try:
            engine.dispose()
        except Exception:
            pass


pytestmark = pytest.mark.skipif(
    not _postgres_disponivel(),
    reason="Postgres de teste indisponível (docker compose up -d postgres)",
)


@pytest.fixture(scope="module")
def engine():
    from sqlalchemy import create_engine

    subprocess.run(
        [".venv/bin/alembic", "downgrade", "base"],
        cwd=REPO, check=False, capture_output=True,
        env={**os.environ, "DATABASE_URL_MIGRATIONS": TEST_URL},
    )
    result = subprocess.run(
        [".venv/bin/alembic", "upgrade", "head"],
        cwd=REPO, capture_output=True, text=True,
        env={**os.environ, "DATABASE_URL_MIGRATIONS": TEST_URL},
    )
    assert result.returncode == 0, result.stderr

    eng = create_engine(TEST_URL, future=True)
    yield eng
    eng.dispose()


@pytest.fixture()
def session(engine):
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as s:
        # limpa entre testes sem violar o append-only (DELETE é bloqueado):
        # TRUNCATE é DDL e roda como dona do schema.
        s.execute(
            text(
                "TRUNCATE catalog.job, catalog.job_schedule, catalog.job_contract_version, "
                "catalog.job_revision, catalog.job_connection_alias, catalog.crontab_snapshot, "
                "catalog.crontab_entry, catalog.reconciliation_run, "
                "catalog.reconciliation_finding, catalog.connection_alias CASCADE"
            )
        )
        s.commit()
        yield s
        s.rollback()


@pytest.fixture()
def resultado(tmp_path):
    """Pacote sintético mínimo, no formato do coletor."""
    fw = "/opt2/batch_v2/batch-commons-framework"
    pkg = tmp_path / "batch-seed-teste-20260910T000000Z"
    (pkg / "framework" / "processes" / "reportes").mkdir(parents=True)
    (pkg / "framework" / "schedulers" / "reportes").mkdir(parents=True)

    contrato = {
        "name_process": "reportes",
        "client": "cliente_a",
        "country": "colombia",
        "environment": "prd",
        "steps": [
            {"step": 1, "function": "download", "stop_on_failed": True, "server": "alias_a"},
            {"step": 2, "function": "upload_remote", "stop_on_failed": False,
             "server_remote": "cliente_downstream"},
            # Alias de conexão de verdade (em `server`) e ausente do
            # connections.json: este é o caso que `alias-nao-declarado` cobre.
            {"step": 3, "function": "upload", "stop_on_failed": False,
             "server": "alias_ausente"},
        ],
    }
    (pkg / "framework" / "processes" / "reportes" / "prd_aaa_col_rpt.json").write_text(
        json.dumps(contrato), encoding="utf-8"
    )
    (pkg / "framework" / "schedulers" / "reportes" / "prd_aaa_col_rpt.sh").write_text(
        f"#!/bin/bash\n{fw}/main.sh --process-file {fw}/processes/reportes/prd_aaa_col_rpt.json --no-mail\n",
        encoding="utf-8",
    )

    inventory = f"""
##### BEGIN HOST-INFO
hostname_short\tteste
collected_at_utc\t20260910T000000Z
collected_by\troot
etc_localtime\t/usr/share/zoneinfo/America/Lima
collector\tcollect-seed.sh 2.0.1
##### END HOST-INFO

##### BEGIN DETECTED-PATHS
framework_root\t{fw}
schedulers_dir\t{fw}/schedulers
processes_dir\t{fw}/processes
##### END DETECTED-PATHS

##### BEGIN CRONTAB | user=batch_user | source=/var/spool/cron/batch_user
# relatorio diario
00 02 * * * {fw}/schedulers/reportes/prd_aaa_col_rpt.sh >> {fw}/logs/x.log
# desabilitado a pedido do cliente
#30 09 * * * {fw}/schedulers/reportes/prd_aaa_col_rpt.sh >> {fw}/logs/x.log
##### END CRONTAB

##### BEGIN CONNECTION-ALIASES | path={fw}/connections/connections.json
alias_a\tSFTP\t172.17.37.90\tuser_a\t\t\tkey\t/home/batch_user/.ssh/id_rsa\tname,host
##### END CONNECTION-ALIASES
"""
    (pkg / "inventory.txt").write_text(inventory.strip() + "\n", encoding="utf-8")
    return build_catalog(SeedPackage(pkg), Vocabulary.load())


# --- carga -------------------------------------------------------------------

def test_carga_persiste_job_agenda_e_contrato(session, resultado):
    report = load_catalog(resultado, session, actor="teste@dev")
    session.commit()

    assert report.jobs_created == 1
    assert report.schedules_created == 2      # uma ativa, uma desabilitada
    assert report.contract_versions_created == 1

    job = session.scalar(select(Job))
    assert job.client_name == "cliente_a"      # veio do contrato, não do nome
    assert job.country_codes == ["CO"]
    assert job.environment == "PROD"
    assert job.status == "active"
    assert job.current_contract_version_id is not None

    agendas = session.scalars(select(JobSchedule)).all()
    assert {a.enabled for a in agendas} == {True, False}
    desabilitada = next(a for a in agendas if not a.enabled)
    assert desabilitada.status_reason == "desabilitado a pedido do cliente"


def test_carga_e_idempotente(session, resultado):
    load_catalog(resultado, session, actor="teste@dev")
    session.commit()
    antes = session.scalar(select(func.count()).select_from(AuditEvent))

    segunda = load_catalog(resultado, session, actor="teste@dev")
    session.commit()

    assert segunda.jobs_created == 0
    assert segunda.jobs_updated == 0
    assert segunda.jobs_unchanged == 1
    assert segunda.schedules_created == 0
    assert segunda.contract_versions_created == 0
    assert session.scalar(select(func.count()).select_from(Job)) == 1
    assert session.scalar(select(func.count()).select_from(JobRevision)) == 1
    # só o evento do próprio import
    assert session.scalar(select(func.count()).select_from(AuditEvent)) == antes + 1


def test_toda_linha_de_crontab_e_preservada(session, resultado):
    load_catalog(resultado, session, actor="teste@dev")
    session.commit()
    total = session.scalar(select(func.count()).select_from(CrontabEntryRow))
    assert total == len(resultado.cron_entries)
    kinds = set(session.scalars(select(CrontabEntryRow.kind)).all())
    assert "comment" in kinds and "job" in kinds


def test_alias_nao_declarado_fica_marcado(session, resultado):
    load_catalog(resultado, session, actor="teste@dev")
    session.commit()
    rows = session.execute(
        text("select alias_name, declared from catalog.job_connection_alias order by alias_name")
    ).all()
    assert dict(rows) == {"alias_a": True, "alias_ausente": False}
    assert session.scalar(select(func.count()).select_from(ConnectionAlias)) == 1


def test_mudanca_gera_revisao_e_auditoria(session, resultado):
    load_catalog(resultado, session, actor="teste@dev")
    session.commit()

    # simula curadoria: o contrato passa a declarar outro cliente
    for record in resultado.jobs:
        record.client_name = "cliente_a_renomeado"
    report = load_catalog(resultado, session, actor="curador@dev")
    session.commit()

    assert report.jobs_updated == 1
    assert session.scalar(select(func.count()).select_from(JobRevision)) == 2
    acoes = session.scalars(select(AuditEvent.action)).all()
    assert "job.update" in acoes
    evento = session.scalar(select(AuditEvent).where(AuditEvent.action == "job.update"))
    assert evento.actor == "curador@dev"
    assert evento.payload["antes"]["client_name"] == "cliente_a"


def test_dry_run_nao_persiste(session, resultado):
    report = load_catalog(resultado, session, actor="teste@dev", dry_run=True)
    assert report.dry_run
    assert session.scalar(select(func.count()).select_from(Job)) == 0


def test_explicacao_de_finding_sobrevive_a_nova_reconciliacao(session, resultado):
    load_catalog(resultado, session, actor="teste@dev")
    session.commit()

    finding = session.scalar(select(ReconciliationFinding).limit(1))
    assert finding is not None
    session.execute(
        text(
            "update catalog.reconciliation_finding set status='explained', "
            "explanation='esperado: alias sera criado no vault', explained_by='maeverson' "
            "where id = :id"
        ),
        {"id": finding.id},
    )
    session.commit()

    segunda = load_catalog(resultado, session, actor="teste@dev")
    session.commit()
    assert segunda.findings_carried >= 1
    herdado = session.scalars(
        select(ReconciliationFinding).where(ReconciliationFinding.fingerprint == finding.fingerprint)
    ).all()
    assert any(f.status == "explained" and f.explained_by == "maeverson" for f in herdado)


# --- imutabilidade -----------------------------------------------------------

@pytest.mark.parametrize("tabela", ["audit_event", "job_contract_version", "job_revision"])
def test_tabelas_append_only_recusam_update_e_delete(session, resultado, tabela):
    load_catalog(resultado, session, actor="teste@dev")
    session.commit()

    for comando in (f"update catalog.{tabela} set created_by = 'x'"
                    if tabela != "audit_event" else f"update catalog.{tabela} set actor = 'x'",
                    f"delete from catalog.{tabela}"):
        with pytest.raises(Exception) as erro:
            session.execute(text(comando))
            session.commit()
        session.rollback()
        assert "append-only" in str(erro.value).lower() or "permission denied" in str(erro.value).lower()


def test_update_direto_na_particao_de_auditoria_e_bloqueado(session, resultado):
    load_catalog(resultado, session, actor="teste@dev")
    session.commit()
    particao = session.scalar(
        text("select tableoid::regclass::text from catalog.audit_event limit 1")
    )
    assert particao and particao != "catalog.audit_event"
    with pytest.raises(Exception) as erro:
        session.execute(text(f"update {particao} set actor = 'adulterado'"))
        session.commit()
    session.rollback()
    assert "append-only" in str(erro.value).lower()


# --- drift entre modelo e banco ---------------------------------------------

def test_modelo_e_migration_nao_divergem(engine):
    """Migration escrita à mão + modelo ORM podem divergir; isto acusa."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from catalog.db.models import Base

    with engine.connect() as connection:
        contexto = MigrationContext.configure(
            connection,
            opts={
                "include_schemas": True,
                "version_table_schema": "catalog",
                "compare_type": True,
                "include_object": lambda obj, name, type_, reflected, compare_to: not (
                    type_ == "table" and str(name).startswith("audit_event_")
                ),
            },
        )
        diferencas = compare_metadata(contexto, Base.metadata)

    # o schema `public` do Postgres e a tabela de versão do Alembic não contam
    relevantes = [
        d for d in diferencas
        if "alembic_version" not in str(d) and "'public'" not in str(d)
    ]
    assert not relevantes, f"modelo e banco divergem: {relevantes}"


# =============================================================================
# Reconciliação crontab × catálogo (SPEC requisito 5)
#
# O que estes testes travam: reconciliar LÊ o catálogo e não o altera. Se o job
# de diff pudesse escrever, ele consertaria a divergência que deveria reportar —
# e o relatório nunca acusaria nada.
# =============================================================================

FW = "/opt2/batch_v2/batch-commons-framework"

CONTRATO_BASE = {
    "name_process": "reportes",
    "client": "cliente_a",
    "country": "colombia",
    "environment": "prd",
    "description": "relatorio diario",
    "send_infra_mail": "BOTH",
    "steps": [
        {"step": 1, "function": "download", "stop_on_failed": True, "server": "alias_a",
         "files": [{"file_name": "rpt_@@@YYYYMMDD@@@.txt", "remote_path": "/out",
                    "local_path": "."}]},
    ],
}


def _pacote(raiz: Path, *, crontab: str, contrato: dict | None = None,
            nome: str = "prd_aaa_col_rpt", host: str = "teste"):
    """Pacote de coleta sintético, parametrizável — a base de todo diff."""
    pkg = raiz / "batch-seed-teste-20260910T000000Z"
    (pkg / "framework" / "processes" / "reportes").mkdir(parents=True, exist_ok=True)
    (pkg / "framework" / "schedulers" / "reportes").mkdir(parents=True, exist_ok=True)

    (pkg / "framework" / "processes" / "reportes" / f"{nome}.json").write_text(
        json.dumps(contrato if contrato is not None else CONTRATO_BASE), encoding="utf-8"
    )
    (pkg / "framework" / "schedulers" / "reportes" / f"{nome}.sh").write_text(
        f"#!/bin/bash\n{FW}/main.sh --process-file {FW}/processes/reportes/{nome}.json --no-mail\n",
        encoding="utf-8",
    )

    inventory = f"""
##### BEGIN HOST-INFO
hostname_short\t{host}
collected_at_utc\t20260910T000000Z
collected_by\troot
etc_localtime\t/usr/share/zoneinfo/America/Lima
collector\tcollect-seed.sh 2.0.1
##### END HOST-INFO

##### BEGIN DETECTED-PATHS
framework_root\t{FW}
schedulers_dir\t{FW}/schedulers
processes_dir\t{FW}/processes
##### END DETECTED-PATHS

##### BEGIN CRONTAB | user=batch_user | source=/var/spool/cron/batch_user
{crontab.strip()}
##### END CRONTAB

##### BEGIN CONNECTION-ALIASES | path={FW}/connections/connections.json
alias_a\tSFTP\t172.17.37.90\tuser_a\t\t\tkey\t/home/batch_user/.ssh/id_rsa\tname,host
##### END CONNECTION-ALIASES
"""
    (pkg / "inventory.txt").write_text(inventory.strip() + "\n", encoding="utf-8")
    return build_catalog(SeedPackage(pkg), Vocabulary.load())


CRON_BASE = f"00 02 * * * {FW}/schedulers/reportes/prd_aaa_col_rpt.sh >> {FW}/logs/x.log"


@pytest.fixture()
def catalogado(session, tmp_path):
    """Catálogo já carregado e em dia com a coleta."""
    resultado = _pacote(tmp_path / "carga", crontab=CRON_BASE)
    load_catalog(resultado, session, actor="carga@dev")
    session.commit()
    return resultado


def _tipos(report):
    return {d.kind for d in report.divergences}


def test_reconcile_em_dia_nao_acusa_divergencia_estrutural(catalogado, session, tmp_path):
    from catalog.db.reconcile import reconcile

    report = reconcile(_pacote(tmp_path / "b", crontab=CRON_BASE), session, actor="recon")
    session.commit()

    estruturais = _tipos(report) & {
        "job-fantasma", "job-sem-entrada-no-crontab",
        "agenda-divergente", "contrato-divergente", "metadado-divergente",
    }
    assert estruturais == set()
    assert report.jobs_in_catalog == report.jobs_in_crontab == 1


def test_reconcile_nao_escreve_catalogo(catalogado, session, tmp_path):
    """O diff é leitura. Se escrevesse, consertaria o que deveria reportar."""
    from catalog.db.reconcile import reconcile

    outro = f"{CRON_BASE}\n30 05 * * * {FW}/schedulers/reportes/prd_bbb_col_rpt.sh >> {FW}/logs/y.log"
    antes_jobs = session.scalar(select(func.count()).select_from(Job))
    antes_versoes = session.scalar(select(func.count()).select_from(JobContractVersion))

    reconcile(_pacote(tmp_path / "b", crontab=outro), session, actor="recon")
    session.commit()

    assert session.scalar(select(func.count()).select_from(Job)) == antes_jobs
    assert session.scalar(select(func.count()).select_from(JobContractVersion)) == antes_versoes


def test_job_fantasma_e_erro_quando_ativo(catalogado, session, tmp_path):
    """Crontab agenda o que o catálogo não conhece: execução fora de governança."""
    from catalog.db.reconcile import reconcile

    novo = f"{CRON_BASE}\n30 05 * * * {FW}/schedulers/reportes/prd_bbb_col_rpt.sh >> {FW}/logs/y.log"
    report = reconcile(_pacote(tmp_path / "b", crontab=novo), session, actor="recon")
    session.commit()

    fantasmas = [d for d in report.divergences if d.kind == "job-fantasma"]
    assert len(fantasmas) == 1
    assert fantasmas[0].severity is Severity.ERROR
    assert report.open_errors >= 1


def test_job_do_catalogo_ausente_da_coleta(catalogado, session, tmp_path):
    from catalog.db.reconcile import reconcile

    vazio = f"# nada agendado\n00 03 * * * {FW}/clean_logs.sh"
    report = reconcile(_pacote(tmp_path / "b", crontab=vazio), session, actor="recon")
    session.commit()

    ausentes = [d for d in report.divergences if d.kind == "job-sem-entrada-no-crontab"]
    assert len(ausentes) == 1
    assert ausentes[0].severity is Severity.ERROR   # estava ativo no catálogo


def test_agenda_editada_a_mao_e_divergencia(catalogado, session, tmp_path):
    from catalog.db.reconcile import reconcile

    mudou = CRON_BASE.replace("00 02", "45 23")
    report = reconcile(_pacote(tmp_path / "b", crontab=mudou), session, actor="recon")
    session.commit()

    agendas = [d for d in report.divergences if d.kind == "agenda-divergente"]
    assert agendas and any("schedule_expr" in d.detail for d in agendas)


def test_contrato_alterado_em_disco_e_divergencia(catalogado, session, tmp_path):
    """Contrato mudou sem passar pela plataforma — o hash denuncia."""
    from catalog.db.reconcile import reconcile

    alterado = json.loads(json.dumps(CONTRATO_BASE))
    alterado["steps"].append({
        "step": 2, "function": "upload_remote", "stop_on_failed": False,
        "server_remote": "cliente_externo", "files": [{"file_name": "x.txt"}],
    })
    report = reconcile(
        _pacote(tmp_path / "b", crontab=CRON_BASE, contrato=alterado), session, actor="recon"
    )
    session.commit()

    contratos = [d for d in report.divergences if d.kind == "contrato-divergente"]
    assert len(contratos) == 1
    assert contratos[0].severity is Severity.ERROR


def test_reconcile_gera_run_findings_e_auditoria(catalogado, session, tmp_path):
    from catalog.db.reconcile import reconcile

    novo = f"{CRON_BASE}\n30 05 * * * {FW}/schedulers/reportes/prd_bbb_col_rpt.sh >> {FW}/logs/y.log"
    antes = session.scalar(select(func.count()).select_from(AuditEvent))
    report = reconcile(_pacote(tmp_path / "b", crontab=novo), session, actor="recon@dev")
    session.commit()

    assert report.run_id is not None
    assert report.divergences
    gravados = session.scalar(
        select(func.count()).select_from(ReconciliationFinding)
        .where(ReconciliationFinding.run_id == report.run_id)
    )
    assert gravados == len(report.divergences)

    # `audit_event` é append-only e não é truncado entre testes: o filtro é
    # pelo ator deste teste, não pela contagem global.
    evento = session.scalars(
        select(AuditEvent).where(AuditEvent.action == "catalog.reconcile",
                                 AuditEvent.actor == "recon@dev")
    ).all()
    assert len(evento) == 1
    assert evento[0].payload["run_id"] == report.run_id
    assert session.scalar(select(func.count()).select_from(AuditEvent)) > antes


def test_dry_run_nao_persiste_run(catalogado, session, tmp_path):
    from catalog.db.reconcile import reconcile

    antes = session.scalar(select(func.count()).select_from(ReconciliationFinding))
    report = reconcile(
        _pacote(tmp_path / "b", crontab=CRON_BASE), session, actor="recon", dry_run=True
    )
    session.commit()

    assert report.run_id is None
    assert session.scalar(select(func.count()).select_from(ReconciliationFinding)) == antes


def test_explicacao_sobrevive_ao_proximo_run(catalogado, session, tmp_path):
    """Critério de aceite da fase é 'sem divergência NÃO EXPLICADA' — logo a
    explicação tem de atravessar runs, senão o critério é inatingível."""
    from catalog.db.reconcile import explain_finding, reconcile

    novo = f"{CRON_BASE}\n30 05 * * * {FW}/schedulers/reportes/prd_bbb_col_rpt.sh >> {FW}/logs/y.log"
    primeiro = reconcile(_pacote(tmp_path / "b", crontab=novo), session, actor="recon")
    session.commit()
    assert primeiro.open_errors >= 1

    fantasma = session.scalars(
        select(ReconciliationFinding)
        .where(ReconciliationFinding.run_id == primeiro.run_id,
               ReconciliationFinding.kind == "job-fantasma")
    ).one()
    total = explain_finding(session, fantasma.fingerprint,
                            explanation="job novo, entra no catalogo na CDPP-1",
                            actor="eu@dev")
    session.commit()
    assert total == 1

    segundo = reconcile(_pacote(tmp_path / "c", crontab=novo), session, actor="recon")
    session.commit()

    herdada = session.scalars(
        select(ReconciliationFinding)
        .where(ReconciliationFinding.run_id == segundo.run_id,
               ReconciliationFinding.kind == "job-fantasma")
    ).one()
    assert herdada.status == "explained"
    assert herdada.explained_by == "eu@dev"
    assert segundo.explained_count >= 1


def test_explicar_e_auditado(catalogado, session, tmp_path):
    from catalog.db.reconcile import explain_finding, reconcile

    novo = f"{CRON_BASE}\n30 05 * * * {FW}/schedulers/reportes/prd_bbb_col_rpt.sh >> {FW}/logs/y.log"
    report = reconcile(_pacote(tmp_path / "b", crontab=novo), session, actor="recon")
    session.commit()
    alvo = session.scalars(
        select(ReconciliationFinding).where(ReconciliationFinding.run_id == report.run_id)
    ).first()

    explain_finding(session, alvo.fingerprint, explanation="conhecido", actor="explica@dev")
    session.commit()

    evento = session.scalars(
        select(AuditEvent).where(AuditEvent.action == "reconciliation.finding.explain",
                                 AuditEvent.actor == "explica@dev")
    ).one()
    assert evento.actor == "explica@dev"
    assert evento.payload["explanation"] == "conhecido"


# --- veredito do schema persistido junto da versão ---------------------------

def test_versao_de_contrato_guarda_o_veredito(session, tmp_path):
    resultado = _pacote(tmp_path / "carga", crontab=CRON_BASE)
    load_catalog(resultado, session, actor="carga@dev")
    session.commit()

    versao = session.scalar(select(JobContractVersion))
    assert versao.validation_status == "valid"
    assert versao.validation["schema_revision"]
    assert versao.validation["violations"] == []


def test_contrato_invalido_entra_no_catalogo_sob_politica_legacy(session, tmp_path):
    """O catálogo registra o parque como ele é — inclusive o que está quebrado."""
    quebrado = json.loads(json.dumps(CONTRATO_BASE))
    quebrado["steps"][0].pop("server")          # download sem servidor: não executa

    resultado = _pacote(tmp_path / "carga", crontab=CRON_BASE, contrato=quebrado)
    report = load_catalog(resultado, session, actor="carga@dev")
    session.commit()

    assert report.jobs_created == 1
    assert report.contracts_invalid == 1
    versao = session.scalar(select(JobContractVersion))
    assert versao.validation_status == "invalid"
    assert versao.validation["violations"]


def test_politica_strict_aborta_a_carga(session, tmp_path):
    from catalog.contract_schema import ContractInvalid, Policy

    quebrado = json.loads(json.dumps(CONTRATO_BASE))
    quebrado["steps"][0].pop("server")
    resultado = _pacote(tmp_path / "carga", crontab=CRON_BASE, contrato=quebrado)

    with pytest.raises(ContractInvalid):
        load_catalog(resultado, session, actor="carga@dev", policy=Policy.STRICT)
    session.rollback()

    assert session.scalar(select(func.count()).select_from(Job)) == 0


def test_reconciliar_um_host_nao_acusa_os_jobs_do_outro(catalogado, session, tmp_path):
    """Dois hosts convivem no mesmo catálogo (PROD e UAT entram juntos na Fase 1).

    A coleta é sempre de UM host, então reconciliar UAT não pode concluir que os
    jobs de PROD sumiram do crontab — seria um relatório de 527 divergências
    falsas a cada execução, e o critério "sem divergência não explicada" viraria
    ruído permanente.
    """
    from catalog.db.reconcile import reconcile

    # `catalogado` já carregou o host `teste`. Agora entra um segundo host.
    uat = _pacote(
        tmp_path / "uat",
        crontab=f"15 04 * * * {FW}/schedulers/reportes/uat_bbb_col_rpt.sh >> {FW}/logs/u.log",
        contrato={**CONTRATO_BASE, "environment": "uat"},
        nome="uat_bbb_col_rpt", host="batch-dtu",
    )
    load_catalog(uat, session, actor="carga@dev")
    session.commit()

    assert {j.host for j in session.scalars(select(Job))} == {"teste", "batch-dtu"}
    assert {j.environment for j in session.scalars(select(Job))} == {"PROD", "UAT"}

    # Reconciliar só o host de UAT: o job de PROD não é problema de UAT.
    report = reconcile(uat, session, actor="recon")
    session.commit()

    assert report.jobs_in_catalog == 1          # só os do host reconciliado
    assert not [d for d in report.divergences if d.kind == "job-sem-entrada-no-crontab"]
    assert not [d for d in report.divergences if d.kind == "job-fantasma"]
