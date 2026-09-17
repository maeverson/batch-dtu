"""`catalog ingest-processes` — carga a partir da árvore de contratos.

A fonte aqui é um diretório de `.json` (ex.: `processes/base2` do host), não o
pacote do coletor. O que este teste trava:

* **idempotência** — rodar duas vezes não duplica job nem versão;
* **caminho do host** — o `contract_path` gravado é o do HOST, não o da cópia
  local de onde se leu (é ele que vai para `--process-file`);
* **honestidade** — sem crontab não há agenda, então nada de `job_schedule`, e
  contrato que sumiu do diretório NÃO é apagado do catálogo.

Pula automaticamente se o Postgres do compose não estiver no ar.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from catalog.db.models import AuditEvent, Job, JobContractVersion, JobSchedule
from catalog.ingest_processes import ingest_directory

TEST_URL = os.environ.get(
    "DATABASE_URL_TEST",
    "postgresql+psycopg://batch_migrator:batch_migrator_dev@localhost:5432/batch_catalog_test",
)
REPO = Path(__file__).resolve().parents[1]
HOST = "srv-sftp-2"


def _postgres_disponivel() -> bool:
    try:
        from sqlalchemy import create_engine

        engine = create_engine(TEST_URL, connect_args={"connect_timeout": 2})
        with engine.connect():
            return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_disponivel(),
    reason="Postgres de teste indisponível (docker compose up -d postgres)",
)


@pytest.fixture(scope="module")
def engine():
    from sqlalchemy import create_engine

    subprocess.run([".venv/bin/alembic", "downgrade", "base"], cwd=REPO,
                   check=False, capture_output=True,
                   env={**os.environ, "DATABASE_URL_MIGRATIONS": TEST_URL})
    resultado = subprocess.run([".venv/bin/alembic", "upgrade", "head"], cwd=REPO,
                               capture_output=True, text=True,
                               env={**os.environ, "DATABASE_URL_MIGRATIONS": TEST_URL})
    assert resultado.returncode == 0, resultado.stderr

    eng = create_engine(TEST_URL, future=True)
    yield eng
    eng.dispose()


@pytest.fixture()
def session(engine):
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as s:
        s.execute(text(
            "TRUNCATE catalog.job, catalog.job_schedule, catalog.job_contract_version, "
            "catalog.job_revision, catalog.job_connection_alias, catalog.audit_event CASCADE"
        ))
        s.commit()
        yield s
        s.rollback()


def _contrato(nome: str = "base2", cliente: str = "cliente_a") -> dict:
    return {
        "name_process": nome, "client": cliente, "country": "colombia",
        "environment": "prd", "description": "teste", "send_infra_mail": "NONE",
        "steps": [
            {"step": 1, "function": "download", "stop_on_failed": True, "server": "alias_a",
             "files": [{"file_name": "r.txt", "remote_path": "/out", "local_path": "."}]},
        ],
    }


@pytest.fixture()
def diretorio(tmp_path: Path) -> Path:
    base2 = tmp_path / "base2"
    base2.mkdir()
    (base2 / "prd_aaa_col_bs2.json").write_text(json.dumps(_contrato()), encoding="utf-8")
    (base2 / "prd_bbb_col_bs2.json").write_text(
        json.dumps(_contrato(cliente="cliente_b")), encoding="utf-8"
    )
    return base2


def test_carrega_jobs_e_contratos(session, diretorio):
    report = ingest_directory(diretorio, session, host=HOST, environment="PROD", actor="teste")
    session.commit()

    assert report.files_read == 2
    assert report.jobs_created == 2
    assert report.versions_created == 2
    assert report.domain == "base2"

    jobs = list(session.scalars(select(Job).order_by(Job.process_name)))
    assert [j.process_name for j in jobs] == ["prd_aaa_col_bs2", "prd_bbb_col_bs2"]
    assert all(j.environment == "PROD" and j.host == HOST for j in jobs)
    assert all(j.current_contract_version_id is not None for j in jobs)
    # Cliente vem do campo `client` do contrato — nunca adivinhado do nome.
    assert {j.client_code for j in jobs} == {"cliente_a", "cliente_b"}


def test_contract_path_e_o_caminho_do_host_nao_o_local(session, diretorio):
    """Ler de uma cópia local não pode mudar o caminho gravado: é ele que vai
    para `--process-file` na execução."""
    ingest_directory(diretorio, session, host=HOST, environment="PROD", actor="teste")
    session.commit()

    caminhos = list(session.scalars(select(Job.contract_path)))
    assert all(
        c.startswith("/opt2/batch_v2/batch-commons-framework/processes/base2/") for c in caminhos
    )
    assert not any(str(diretorio) in c for c in caminhos)


def test_framework_root_customizado(session, diretorio):
    ingest_directory(diretorio, session, host=HOST, environment="UAT",
                     framework_root="/opt/outro", actor="teste")
    session.commit()
    caminhos = list(session.scalars(select(Job.contract_path)))
    assert all(c.startswith("/opt/outro/processes/base2/") for c in caminhos)


def test_idempotente(session, diretorio):
    ingest_directory(diretorio, session, host=HOST, environment="PROD", actor="teste")
    session.commit()
    segundo = ingest_directory(diretorio, session, host=HOST, environment="PROD", actor="teste")
    session.commit()

    assert segundo.jobs_created == 0
    assert segundo.versions_created == 0
    assert segundo.versions_reused == 2
    assert session.scalar(select(func.count()).select_from(Job)) == 2
    assert session.scalar(select(func.count()).select_from(JobContractVersion)) == 2


def test_contrato_alterado_gera_nova_versao(session, diretorio):
    ingest_directory(diretorio, session, host=HOST, environment="PROD", actor="teste")
    session.commit()

    contrato = _contrato()
    contrato["description"] = "mudou"
    (diretorio / "prd_aaa_col_bs2.json").write_text(json.dumps(contrato), encoding="utf-8")

    report = ingest_directory(diretorio, session, host=HOST, environment="PROD", actor="teste")
    session.commit()

    assert report.versions_created == 1
    versoes = list(session.scalars(
        select(JobContractVersion).order_by(JobContractVersion.version)
    ))
    assert [v.version for v in versoes if v.job.process_name == "prd_aaa_col_bs2"] == [1, 2]


def test_nao_inventa_agenda(session, diretorio):
    ingest_directory(diretorio, session, host=HOST, environment="PROD", actor="teste")
    session.commit()
    # Sem crontab não há como saber quando o job roda.
    assert session.scalar(select(func.count()).select_from(JobSchedule)) == 0
    assert all(j.status == "on_demand" for j in session.scalars(select(Job)))


def test_contrato_removido_do_diretorio_e_relatado_nao_apagado(session, diretorio):
    ingest_directory(diretorio, session, host=HOST, environment="PROD", actor="teste")
    session.commit()

    (diretorio / "prd_bbb_col_bs2.json").unlink()
    report = ingest_directory(diretorio, session, host=HOST, environment="PROD", actor="teste")
    session.commit()

    assert report.missing_in_directory == [
        "/opt2/batch_v2/batch-commons-framework/processes/base2/prd_bbb_col_bs2.json"
    ]
    assert session.scalar(select(func.count()).select_from(Job)) == 2   # nada apagado


def test_json_quebrado_nao_entra_e_e_reportado(session, diretorio):
    (diretorio / "quebrado.json").write_text("{isto nao e json", encoding="utf-8")
    report = ingest_directory(diretorio, session, host=HOST, environment="PROD", actor="teste")
    session.commit()

    assert len(report.invalid_json) == 1
    assert not report.ok
    assert session.scalar(select(func.count()).select_from(Job)) == 2   # só os dois válidos


def test_contrato_invalido_no_schema_entra_com_veredito(session, diretorio):
    """O catálogo mostra o parque como ele é, inclusive quebrado — mas o
    veredito fica gravado na versão (mesma política do `catalog load`)."""
    (diretorio / "sem_steps.json").write_text(
        json.dumps({"name_process": "base2", "client": "cliente_c"}), encoding="utf-8"
    )
    report = ingest_directory(diretorio, session, host=HOST, environment="PROD", actor="teste")
    session.commit()

    assert len(report.schema_invalid) == 1
    job = session.scalar(select(Job).where(Job.process_name == "sem_steps"))
    versao = session.get(JobContractVersion, job.current_contract_version_id)
    assert versao.validation_status == "invalid"


def test_grava_auditoria(session, diretorio):
    report = ingest_directory(diretorio, session, host=HOST, environment="PROD", actor="maeverson")
    session.commit()

    acoes = list(session.scalars(select(AuditEvent.action)))
    assert acoes.count("job.create") == 2
    assert acoes.count("job.contract_version") == 2
    assert report.audit_events == len(acoes)
    assert all(a == "maeverson" for a in session.scalars(select(AuditEvent.actor)))


def test_dry_run_nao_persiste(session, diretorio):
    report = ingest_directory(diretorio, session, host=HOST, environment="PROD",
                              actor="teste", dry_run=True)
    assert report.jobs_created == 2
    assert session.scalar(select(func.count()).select_from(Job)) == 0
