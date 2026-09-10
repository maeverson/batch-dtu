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

from catalog.build import build_catalog
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
             "server_remote": "alias_ausente"},
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
