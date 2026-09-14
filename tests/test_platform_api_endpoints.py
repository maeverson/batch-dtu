"""Endpoints da Platform API contra Postgres real + Keycloak real (o do
compose, `docker compose --profile auth up`) + `InMemoryExecutionBackend`
(sem SSH — isso já é coberto por `test_platform_api_ssh_integration.py`).

Pula automaticamente se Postgres ou Keycloak não estiverem no ar — mesmo
padrão de `test_db_catalog.py`.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

TEST_URL = os.environ.get(
    "DATABASE_URL_TEST",
    "postgresql+psycopg://batch_migrator:batch_migrator_dev@localhost:5432/batch_catalog_test",
)
REPO = Path(__file__).resolve().parents[1]
KEYCLOAK_URL = os.environ.get("KEYCLOAK_URL", "http://localhost:8080")
REALM = "batch-dtu"
CLIENT_ID = "platform-api"
CLIENT_SECRET = "platform-api-dev-secret"


def _postgres_disponivel() -> bool:
    try:
        from sqlalchemy import create_engine
        engine = create_engine(TEST_URL, connect_args={"connect_timeout": 2})
        with engine.connect():
            return True
    except Exception:
        return False


def _keycloak_disponivel() -> bool:
    try:
        import httpx
        r = httpx.get(f"{KEYCLOAK_URL}/realms/{REALM}/.well-known/openid-configuration", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


pytestmark = [
    pytest.mark.skipif(not _postgres_disponivel(),
                       reason="Postgres de teste indisponível"),
    pytest.mark.skipif(not _keycloak_disponivel(),
                       reason="Keycloak indisponível (docker compose --profile auth up -d)"),
]


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
    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as s:
        s.execute(text(
            "TRUNCATE catalog.job, catalog.job_schedule, catalog.job_contract_version, "
            "catalog.job_revision, catalog.job_connection_alias, catalog.crontab_snapshot, "
            "catalog.crontab_entry, catalog.reconciliation_run, catalog.reconciliation_finding, "
            "catalog.connection_alias, catalog.role_binding, catalog.execution, "
            "catalog.crontab_change_request, catalog.audit_event CASCADE"
        ))
        s.commit()
        yield s
        s.rollback()


@pytest.fixture()
def app(engine):
    from platform_api.app import create_app
    from platform_api.config import Settings
    from platform_api.ssh_backend import InMemoryExecutionBackend

    backend = InMemoryExecutionBackend()
    aplicativo = create_app(Settings.from_env(), execution_backend=backend, engine=engine)
    aplicativo.state.execution_backend = backend   # referência estável p/ os testes inspecionarem
    return aplicativo


@pytest.fixture()
def client(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c


def _token(username: str, password: str | None = None) -> str:
    import httpx

    r = httpx.post(
        f"{KEYCLOAK_URL}/realms/{REALM}/protocol/openid-connect/token",
        data={"grant_type": "password", "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
              "username": username, "password": password or username, "scope": "openid"},
        timeout=5,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _auth(username: str) -> dict:
    return {"Authorization": f"Bearer {_token(username)}"}


FW = "/opt2/batch_v2/batch-commons-framework"


def _contrato(upload_remote: bool = False) -> dict:
    steps = [
        {"step": 1, "function": "download", "stop_on_failed": True, "server": "alias_a",
         "files": [{"file_name": "r.txt", "remote_path": "/out", "local_path": "."}]},
    ]
    if upload_remote:
        steps.append({
            "step": 2, "function": "upload_remote", "stop_on_failed": False,
            "server_remote": "cliente_x",
            "files": [{"file_name": "s.txt", "source": "s.txt", "remote_path": "/in",
                      "local_path": "."}],
        })
    return {
        "name_process": "reportes", "client": "cliente_a", "country": "colombia",
        "environment": "prd", "description": "teste", "send_infra_mail": "NONE",
        "steps": steps,
    }


def _job(session, *, environment="PROD", domain="reportes", upload_remote=False,
          process_name="prd_aaa_col_rpt", status="active"):
    from catalog.db.models import Job, JobContractVersion

    job = Job(
        host="srv-sftp-2", process_name=process_name,
        contract_path=f"{FW}/processes/{domain}/{process_name}.json",
        domain=domain, environment=environment, status=status, client_name="cliente_a",
    )
    session.add(job)
    session.flush()
    versao = JobContractVersion(
        job_id=job.id, version=1, contract=_contrato(upload_remote),
        contract_hash="x" * 64, contract_bytes=100, steps_count=2 if upload_remote else 1,
        source="seed", created_by="teste", validation_status="valid", validation={},
    )
    session.add(versao)
    session.flush()
    job.current_contract_version_id = versao.id
    session.commit()
    return job


def _binding(session, subject, role, **scope):
    from catalog.db.models import RoleBinding

    b = RoleBinding(subject=subject, role=role, granted_by="teste@dev", **scope)
    session.add(b)
    session.commit()
    return b


# --- catálogo -----------------------------------------------------------

def test_sem_token_e_401(client):
    r = client.get("/jobs")
    assert r.status_code == 401


def test_cors_libera_origem_do_back_office(client):
    # Sem isto, todo fetch do Back Office (localhost:5173) trava no navegador
    # antes de chegar num 401/403 — CORSMiddleware em `app.py`.
    r = client.options(
        "/jobs",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_cors_recusa_origem_desconhecida(client):
    r = client.options(
        "/jobs",
        headers={
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert "access-control-allow-origin" not in r.headers


def test_token_sem_binding_nao_ve_o_job(client, session):
    _job(session)
    r = client.get("/jobs", headers=_auth("viewer"))
    assert r.status_code == 200
    assert r.json() == []   # usuário 'viewer' não tem role_binding nenhum


def test_binding_da_visibilidade(client, session):
    job = _job(session)
    _binding(session, "viewer", "batch.viewer")   # sem escopo = todas
    r = client.get("/jobs", headers=_auth("viewer"))
    assert r.status_code == 200
    assert [j["process_name"] for j in r.json()] == ["prd_aaa_col_rpt"]

    r2 = client.get(f"/jobs/{job.id}", headers=_auth("viewer"))
    assert r2.status_code == 200


def test_binding_escopado_por_dominio_restringe(client, session):
    _job(session, domain="reportes", process_name="prd_a_rpt")
    _job(session, domain="otros", process_name="prd_b_otr")
    _binding(session, "viewer", "batch.viewer", scope_domain="reportes")
    r = client.get("/jobs", headers=_auth("viewer"))
    assert [j["process_name"] for j in r.json()] == ["prd_a_rpt"]


def test_validate_endpoint_roda_o_schema(client, session):
    job = _job(session)
    _binding(session, "viewer", "batch.viewer")
    r = client.post(f"/jobs/{job.id}/validate", headers=_auth("viewer"))
    assert r.status_code == 200
    assert r.json()["status"] == "valid"


# --- PATCH /jobs/{id}/status --------------------------------------------

def test_patch_status_sem_role_e_403(client, session):
    job = _job(session)
    _binding(session, "operator", "batch.operator", scope_environment="UAT")   # não cobre PROD
    r = client.patch(f"/jobs/{job.id}/status", headers=_auth("operator"),
                     json={"desired_status": "disabled", "reason": "teste"})
    assert r.status_code == 403


def test_patch_status_com_role_certa_abre_change_request(client, session):
    job = _job(session, environment="UAT")
    _binding(session, "operator", "batch.operator")
    r = client.patch(f"/jobs/{job.id}/status", headers=_auth("operator"),
                     json={"desired_status": "disabled", "reason": "cliente suspendeu"})
    assert r.status_code == 201
    corpo = r.json()
    assert corpo["state"] == "pending"
    assert f"#BO:{job.id}:{corpo['id']}" in corpo["marker"]
    assert "cliente suspendeu" in corpo["instruction"]

    r2 = client.get(f"/jobs/{job.id}", headers=_auth("operator"))
    assert r2.json()["status"] == "disabled"


def test_prod_exige_operator_prod(client, session):
    job = _job(session, environment="PROD")
    _binding(session, "operator", "batch.operator")   # sem -prod
    r = client.patch(f"/jobs/{job.id}/status", headers=_auth("operator"),
                     json={"desired_status": "disabled", "reason": "teste"})
    assert r.status_code == 403
    assert "operator-prod" in r.json()["detail"]

    _binding(session, "operator-prod", "batch.operator-prod")
    r2 = client.patch(f"/jobs/{job.id}/status", headers=_auth("operator-prod"),
                      json={"desired_status": "disabled", "reason": "teste"})
    assert r2.status_code == 201


# --- POST /executions -----------------------------------------------------

def test_execucao_exige_confirm_target_dates(client, session):
    job = _job(session, environment="UAT")
    _binding(session, "operator", "batch.operator")
    r = client.post("/executions", headers=_auth("operator"), json={
        "job_id": str(job.id), "dates_pattern": ["20260901"],
        "confirm_target_dates": False, "justification": "teste",
    })
    assert r.status_code == 422


def test_execucao_upload_remote_prod_exige_segunda_confirmacao(client, session):
    job = _job(session, environment="PROD", upload_remote=True)
    _binding(session, "operator-prod", "batch.operator-prod")
    r = client.post("/executions", headers=_auth("operator-prod"), json={
        "job_id": str(job.id), "dates_pattern": ["20260901"],
        "confirm_target_dates": True, "justification": "reprocesso",
    })
    assert r.status_code == 422
    assert "confirm_upload_remote" in r.json()["detail"]

    r2 = client.post("/executions", headers=_auth("operator-prod"), json={
        "job_id": str(job.id), "dates_pattern": ["20260901"],
        "confirm_target_dates": True, "confirm_upload_remote": True,
        "justification": "reprocesso",
    })
    assert r2.status_code == 201


def test_execucao_dispara_e_audita(client, session, app):
    job = _job(session, environment="UAT")
    _binding(session, "operator", "batch.operator")
    r = client.post("/executions", headers=_auth("operator"), json={
        "job_id": str(job.id), "dates_pattern": ["20260901"],
        "confirm_target_dates": True, "justification": "reprocesso mensal",
    })
    assert r.status_code == 201, r.text
    corpo = r.json()
    assert corpo["status"] == "succeeded"
    assert corpo["result"] == "success"

    # 2 chamadas ao backend: a pré-validação (--validate-file) e a
    # execução real — ambas passam por build_invocation.
    assert len(app.state.execution_backend.calls) == 2

    from catalog.db.models import AuditEvent
    from sqlalchemy import select
    eventos = list(session.scalars(
        select(AuditEvent).where(AuditEvent.target_id == corpo["id"])
        .order_by(AuditEvent.occurred_at)
    ))
    assert [e.action for e in eventos] == ["execution.dispatch", "execution.completed"]


def test_execucao_e_idempotente(client, session, app):
    job = _job(session, environment="UAT")
    _binding(session, "operator", "batch.operator")
    corpo = {
        "job_id": str(job.id), "dates_pattern": ["20260901"],
        "confirm_target_dates": True, "justification": "reprocesso",
        "idempotency_key": "retry-abc-123",
    }
    r1 = client.post("/executions", headers=_auth("operator"), json=corpo)
    r2 = client.post("/executions", headers=_auth("operator"), json=corpo)
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]
    # 2 na primeira chamada (pré-validação + execução real); a segunda
    # (mesma idempotency_key) devolve a existente sem tocar o backend.
    assert len(app.state.execution_backend.calls) == 2


def test_pre_validacao_reprovada_nao_executa_nem_persiste(client, session, app):
    """`--validate-file` roda SEMPRE antes de executar — se ela falhar, a
    execução real nunca acontece e nada fica gravado."""
    from platform_api.ssh_backend import (
        ExecutionResult,
        InMemoryExecutionBackend,
    )

    class BackendComValidacaoReprovada(InMemoryExecutionBackend):
        async def dispatch(self, request, execution_id):
            from platform_api.ssh_backend import build_invocation
            build_invocation(request, execution_id)
            self.calls.append((request, execution_id))
            if request.validate_only:
                return ExecutionResult(execution_id, 4, "", "contrato invalido")
            return await super().dispatch(request, execution_id)

    app.state.execution_backend = BackendComValidacaoReprovada()
    job = _job(session, environment="UAT")
    _binding(session, "operator", "batch.operator")

    r = client.post("/executions", headers=_auth("operator"), json={
        "job_id": str(job.id), "dates_pattern": ["20260901"],
        "confirm_target_dates": True, "justification": "teste",
    })
    assert r.status_code == 422
    assert "pré-validação" in r.json()["detail"]

    from catalog.db.models import Execution
    from sqlalchemy import func, select
    assert session.scalar(select(func.count()).select_from(Execution)) == 0
    assert len(app.state.execution_backend.calls) == 1   # só a validação, nunca a real


def test_execucao_concorrente_no_mesmo_job_e_409(client, session):
    """`lock por processo` — formalização do SOP Zinli (invariante 7).

    `pg_try_advisory_xact_lock` é por CONEXÃO: `session` (deste teste) e a
    sessão que o endpoint abre (via `session_factory`, outra conexão do pool)
    são conexões distintas — exatamente o cenário de duas requisições
    concorrentes de verdade. O lock desta sessão só libera no rollback do
    teardown do fixture `session`, então ele segue "segurando" durante a
    chamada HTTP abaixo.
    """
    job = _job(session, environment="UAT")
    _binding(session, "operator", "batch.operator")

    from platform_api import locking
    locking.try_lock_job(session, job.id)   # mantém o lock nesta conexão/transação

    r = client.post("/executions", headers=_auth("operator"), json={
        "job_id": str(job.id), "dates_pattern": ["20260901"],
        "confirm_target_dates": True, "justification": "concorrente",
    })
    assert r.status_code == 409


def test_execucao_com_metacaractere_e_422_sem_persistir(client, session):
    job = _job(session, environment="UAT")
    _binding(session, "operator", "batch.operator")
    r = client.post("/executions", headers=_auth("operator"), json={
        "job_id": str(job.id), "steps": "1;rm -rf /", "dates_pattern": ["20260901"],
        "confirm_target_dates": True, "justification": "teste",
    })
    assert r.status_code == 422

    from catalog.db.models import Execution
    from sqlalchemy import func, select
    assert session.scalar(select(func.count()).select_from(Execution)) == 0


# --- change-requests --------------------------------------------------------

def test_listar_e_cancelar_change_request(client, session):
    job = _job(session, environment="UAT")
    _binding(session, "operator", "batch.operator")
    r = client.patch(f"/jobs/{job.id}/status", headers=_auth("operator"),
                     json={"desired_status": "disabled", "reason": "teste"})
    change_id = r.json()["id"]

    r2 = client.get("/change-requests?state=pending", headers=_auth("operator"))
    assert any(c["id"] == change_id for c in r2.json())

    r3 = client.post(f"/change-requests/{change_id}/cancel", headers=_auth("operator"),
                     json={"reason": "decisão revertida"})
    assert r3.status_code == 200
    assert r3.json()["state"] == "cancelled"


# --- auditoria ---------------------------------------------------------

def test_audit_events_exige_admin(client, session):
    _binding(session, "operator", "batch.operator")
    r = client.get("/audit-events", headers=_auth("operator"))
    assert r.status_code == 403

    r2 = client.get("/audit-events", headers=_auth("admin-batch"))
    assert r2.status_code == 200


# --- /me (Back Office, Etapa 1.4) ---------------------------------------

def test_me_sem_binding_nenhum_escopo_visivel(client, session):
    r = client.get("/me", headers=_auth("viewer"))
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["subject"] == "viewer"
    assert "batch.viewer" in corpo["roles"]
    assert corpo["visible_domains"] == []
    assert corpo["visible_environments"] == []


def test_me_binding_sem_escopo_e_todos_os_dominios(client, session):
    _binding(session, "viewer", "batch.viewer")   # sem scope_domain/scope_environment
    r = client.get("/me", headers=_auth("viewer"))
    corpo = r.json()
    assert corpo["visible_domains"] is None          # None = todos
    assert corpo["visible_environments"] is None


def test_me_binding_escopado_lista_so_o_escopo(client, session):
    _binding(session, "operator", "batch.operator", scope_domain="reportes", scope_environment="UAT")
    r = client.get("/me", headers=_auth("operator"))
    corpo = r.json()
    assert corpo["visible_domains"] == ["reportes"]
    assert corpo["visible_environments"] == ["UAT"]


# --- /jobs/{id}/schedules, /contract, /reconciliation (Back Office) ------

def test_job_schedules_endpoint(client, session):
    from catalog.db.models import JobSchedule

    job = _job(session)
    _binding(session, "viewer", "batch.viewer")
    session.add(JobSchedule(
        job_id=job.id, schedule_expr="0 2 * * *", timezone="America/Lima",
        raw_line="0 2 * * * /fw/schedulers/reportes/prd_aaa_col_rpt.sh", created_by="teste",
    ))
    session.commit()

    r = client.get(f"/jobs/{job.id}/schedules", headers=_auth("viewer"))
    assert r.status_code == 200
    assert r.json()[0]["schedule_expr"] == "0 2 * * *"


def test_job_contract_endpoint(client, session):
    job = _job(session)
    _binding(session, "viewer", "batch.viewer")

    r = client.get(f"/jobs/{job.id}/contract", headers=_auth("viewer"))
    assert r.status_code == 200
    assert r.json()["contract"]["name_process"] == "reportes"
    assert r.json()["validation_status"] == "valid"


def test_job_contract_endpoint_sem_versao_e_404(client, session):
    job = _job(session)
    job.current_contract_version_id = None
    session.commit()
    _binding(session, "viewer", "batch.viewer")

    r = client.get(f"/jobs/{job.id}/contract", headers=_auth("viewer"))
    assert r.status_code == 404


def test_job_reconciliation_nunca_rodou(client, session):
    job = _job(session)
    _binding(session, "viewer", "batch.viewer")

    r = client.get(f"/jobs/{job.id}/reconciliation", headers=_auth("viewer"))
    assert r.status_code == 200
    assert r.json()["state"] == "nunca_rodou"
    assert r.json()["open_findings"] == []


def test_job_reconciliation_ok_e_divergente(client, session):
    from catalog.db.models import ReconciliationFinding, ReconciliationRun

    job_ok = _job(session, process_name="prd_ok_col_rpt")
    job_divergente = _job(session, process_name="prd_div_col_rpt")
    _binding(session, "viewer", "batch.viewer")

    run = ReconciliationRun(host="srv-sftp-2", triggered_by="teste")
    session.add(run)
    session.flush()
    session.add(ReconciliationFinding(
        run_id=run.id, kind="job-orfao", severity="erro", subject=job_divergente.process_name,
        job_id=job_divergente.id, status="open", fingerprint="fp-1",
    ))
    session.commit()

    r_ok = client.get(f"/jobs/{job_ok.id}/reconciliation", headers=_auth("viewer"))
    assert r_ok.json()["state"] == "ok"

    r_div = client.get(f"/jobs/{job_divergente.id}/reconciliation", headers=_auth("viewer"))
    corpo = r_div.json()
    assert corpo["state"] == "divergente"
    assert corpo["open_findings"][0]["fingerprint"] == "fp-1"


def test_job_reconciliation_finding_explicado_nao_conta_como_divergente(client, session):
    from catalog.db.models import ReconciliationFinding, ReconciliationRun

    job = _job(session)
    _binding(session, "viewer", "batch.viewer")

    run = ReconciliationRun(host="srv-sftp-2", triggered_by="teste")
    session.add(run)
    session.flush()
    session.add(ReconciliationFinding(
        run_id=run.id, kind="job-orfao", severity="erro", subject=job.process_name,
        job_id=job.id, status="explained", explanation="cliente confirmado", fingerprint="fp-2",
    ))
    session.commit()

    r = client.get(f"/jobs/{job.id}/reconciliation", headers=_auth("viewer"))
    assert r.json()["state"] == "ok"   # achado FECHADO não conta como divergência aberta
