"""`/admin/*` — CRUD de administração do catálogo, só para `batch.admin`.

Por que existe (G6): conceder acesso passou a ser papel do Entra ID, mas
**manter o catálogo** continua sendo trabalho de plataforma, e até aqui a
única forma de criar/corrigir um job era `catalog load` a partir de um pacote
de coleta ou SQL na mão. Este router é a superfície administrativa que faltava,
com as mesmas garantias do resto da API: role verificada no token,
`audit_event` na mesma transação da mudança, e `job_revision` guardando o que
mudou.

Três regras que valem para tudo aqui:

1. **Escopo do deploy vale para o admin também.** Uma instância de UAT não
   cria nem edita job de PROD, nem com `batch.admin`. Criar um job já nasce
   com o `environment`/`host` da instância — não é campo de entrada.
2. **`DELETE` não apaga.** Job tem execução, auditoria e histórico apontando
   para ele; apagar a linha destruiria a trilha. `DELETE` desativa
   (`status='disabled'`) com motivo obrigatório. Remoção física é operação de
   banco, com DBA e ticket, nunca por API.
3. **Contrato entra validado.** `PUT /admin/jobs/{id}/contract` roda o schema
   (`catalog.contract_schema`) antes de gravar, e a versão é append-only por
   hash — regravar o mesmo conteúdo não cria versão nova (invariante 1).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from catalog.contract_schema import validate_contract
from catalog.db.models import AuditEvent, Job, JobContractVersion, JobRevision

from ..authz import Scope
from ..deps import get_current_user, get_session, require_admin_scope
from ..schemas import ErrorOut, JobContractOut, JobOut
from ..security import AuthenticatedUser

router = APIRouter(prefix="/admin", tags=["administracao"])

_STATUS_VALIDOS = ("active", "disabled", "on_demand", "orphan", "broken")
_KINDS_VALIDOS = ("contract_job", "script_task")


class JobCreateIn(BaseModel):
    """`host` e `environment` NÃO entram: vêm da instância (regra 1)."""

    process_name: str = Field(..., min_length=1, max_length=255)
    wrapper_path: str | None = None
    contract_path: str | None = None
    domain: str | None = Field(None, max_length=64)
    client_code: str | None = Field(None, max_length=64)
    client_name: str | None = Field(None, max_length=255)
    country_codes: list[str] = Field(default_factory=list)
    kind: str = "contract_job"
    criticality: str | None = None
    sla: str | None = None
    owner: str | None = None
    status: str = "active"
    status_reason: str | None = None
    reason: str = Field(..., min_length=3, description="Por que este job está sendo criado")


class JobUpdateIn(BaseModel):
    """Só os campos presentes no corpo são tocados (PATCH de verdade)."""

    wrapper_path: str | None = None
    contract_path: str | None = None
    domain: str | None = None
    client_code: str | None = None
    client_name: str | None = None
    country_codes: list[str] | None = None
    kind: str | None = None
    criticality: str | None = None
    sla: str | None = None
    owner: str | None = None
    status: str | None = None
    status_reason: str | None = None
    reason: str = Field(..., min_length=3)


class JobDeleteIn(BaseModel):
    reason: str = Field(..., min_length=3)


class ContractIn(BaseModel):
    contract: dict
    reason: str = Field(..., min_length=3)


def _json_safe(valor):
    """JSONB não aceita UUID/datetime crus — e `snapshot`/`diff` copiam colunas
    do job, que têm os dois."""
    if isinstance(valor, (uuid.UUID, datetime)):
        return str(valor)
    if isinstance(valor, (list, tuple)):
        return [_json_safe(v) for v in valor]
    if isinstance(valor, dict):
        return {k: _json_safe(v) for k, v in valor.items()}
    return valor


def _registrar(session: Session, *, user: AuthenticatedUser, action: str, job: Job,
               payload: dict) -> None:
    session.add(AuditEvent(
        actor=user.subject, action=action, target_type="job", target_id=str(job.id),
        source="api", occurred_at=datetime.now(timezone.utc),
        payload={**payload, "actor_display_name": user.display_name},
    ))


def _nova_revisao(session: Session, job: Job, antes: dict, user: AuthenticatedUser) -> None:
    ultima = session.scalar(
        select(JobRevision.version).where(JobRevision.job_id == job.id)
        .order_by(JobRevision.version.desc()).limit(1)
    )
    session.add(JobRevision(
        job_id=job.id, version=(ultima or 0) + 1,
        snapshot=_json_safe({
            c.name: getattr(job, c.name)
            for c in Job.__table__.columns
            if c.name not in ("id", "created_at", "updated_at")
        }),
        diff=_json_safe(antes), created_by=user.subject,
    ))


def _job_da_instancia(job_id: uuid.UUID, session: Session, scope: Scope) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"job {job_id} não encontrado")
    if not scope.serves(job):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"job de {job.host}/{job.environment} não é administrado por esta instância "
            f"({scope.host}/{scope.environment})",
        )
    return job


@router.get("/jobs", response_model=list[JobOut])
def listar(
    q: str | None = None,
    domain: str | None = None,
    status_: str | None = None,
    limit: int = 500,
    session: Session = Depends(get_session),
    scope: Scope = Depends(require_admin_scope),
) -> list[Job]:
    consulta = select(Job).where(Job.environment == scope.environment, Job.host == scope.host)
    if q:
        consulta = consulta.where(Job.process_name.ilike(f"%{q}%"))
    if domain:
        consulta = consulta.where(Job.domain == domain)
    if status_:
        consulta = consulta.where(Job.status == status_)
    return list(session.scalars(consulta.order_by(Job.process_name).limit(min(limit, 2000))))


@router.post("/jobs", response_model=JobOut, status_code=status.HTTP_201_CREATED,
             responses={409: {"model": ErrorOut}, 422: {"model": ErrorOut}})
def criar(
    corpo: JobCreateIn,
    session: Session = Depends(get_session),
    scope: Scope = Depends(require_admin_scope),
    user: AuthenticatedUser = Depends(get_current_user),
) -> Job:
    if corpo.status not in _STATUS_VALIDOS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"status deve ser um de {_STATUS_VALIDOS}")
    if corpo.kind not in _KINDS_VALIDOS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"kind deve ser um de {_KINDS_VALIDOS}")
    if corpo.contract_path and "/processes/" not in corpo.contract_path:
        # Mesma regra de `ssh_backend.build_invocation`: um contract_path fora
        # de processes/ cria um job que a execução sempre recusaria.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "contract_path precisa estar sob .../processes/ — do contrário o job "
            "nunca poderá ser executado pelo wrapper",
        )

    duplicado = session.scalar(
        select(Job).where(
            Job.host == scope.host,
            Job.wrapper_path == corpo.wrapper_path,
            Job.contract_path == corpo.contract_path,
        ).limit(1)
    )
    if duplicado is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"já existe job com este wrapper/contrato neste host ({duplicado.id})",
        )

    job = Job(
        host=scope.host, environment=scope.environment,
        process_name=corpo.process_name, wrapper_path=corpo.wrapper_path,
        contract_path=corpo.contract_path, domain=corpo.domain,
        client_code=corpo.client_code, client_name=corpo.client_name,
        country_codes=corpo.country_codes, kind=corpo.kind,
        criticality=corpo.criticality, sla=corpo.sla, owner=corpo.owner,
        status=corpo.status, status_reason=corpo.status_reason,
        created_by=user.subject, updated_by=user.subject,
    )
    session.add(job)
    session.flush()
    _nova_revisao(session, job, antes={}, user=user)
    _registrar(session, user=user, action="job.create", job=job,
               payload={"reason": corpo.reason, "process_name": job.process_name})
    session.flush()
    return job


@router.patch("/jobs/{job_id}", response_model=JobOut, responses={404: {"model": ErrorOut}})
def atualizar(
    job_id: uuid.UUID,
    corpo: JobUpdateIn,
    session: Session = Depends(get_session),
    scope: Scope = Depends(require_admin_scope),
    user: AuthenticatedUser = Depends(get_current_user),
) -> Job:
    job = _job_da_instancia(job_id, session, scope)

    campos = corpo.model_dump(exclude_unset=True, exclude={"reason"})
    if not campos:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "nenhum campo para atualizar")
    if "status" in campos and campos["status"] not in _STATUS_VALIDOS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"status deve ser um de {_STATUS_VALIDOS}")
    if "kind" in campos and campos["kind"] not in _KINDS_VALIDOS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"kind deve ser um de {_KINDS_VALIDOS}")

    antes = {campo: getattr(job, campo) for campo in campos}
    for campo, valor in campos.items():
        setattr(job, campo, valor)
    job.updated_by = user.subject

    _nova_revisao(session, job, antes=antes, user=user)
    _registrar(session, user=user, action="job.update", job=job,
               payload={"reason": corpo.reason, "before": _json_safe(antes),
                        "after": _json_safe(campos)})
    session.flush()
    return job


@router.delete("/jobs/{job_id}", response_model=JobOut, responses={404: {"model": ErrorOut}})
def desativar(
    job_id: uuid.UUID,
    corpo: JobDeleteIn,
    session: Session = Depends(get_session),
    scope: Scope = Depends(require_admin_scope),
    user: AuthenticatedUser = Depends(get_current_user),
) -> Job:
    """Desativa (regra 2 — `DELETE` não apaga linha nenhuma).

    Atenção operacional: isto muda o CATÁLOGO, não o crontab. Enquanto a
    entrada no crontab existir, o job continua rodando no host — quem fecha
    esse loop é `PATCH /jobs/{id}/status`, que abre `crontab_change_request`.
    """
    job = _job_da_instancia(job_id, session, scope)
    if job.status == "disabled":
        raise HTTPException(status.HTTP_409_CONFLICT, "job já está desabilitado no catálogo")

    antes = {"status": job.status, "status_reason": job.status_reason}
    job.status = "disabled"
    job.status_reason = corpo.reason
    job.updated_by = user.subject

    _nova_revisao(session, job, antes=antes, user=user)
    _registrar(session, user=user, action="job.disable", job=job,
               payload={"reason": corpo.reason, "before": _json_safe(antes),
                        "aviso": "catálogo apenas — a entrada do crontab continua ativa"})
    session.flush()
    return job


@router.put("/jobs/{job_id}/contract", response_model=JobContractOut,
            responses={404: {"model": ErrorOut}, 422: {"model": ErrorOut}})
def publicar_contrato(
    job_id: uuid.UUID,
    corpo: ContractIn,
    session: Session = Depends(get_session),
    scope: Scope = Depends(require_admin_scope),
    user: AuthenticatedUser = Depends(get_current_user),
) -> JobContractVersion:
    """Nova versão do contrato JSON — validada pelo schema, append-only por hash.

    Não escreve o arquivo no host: o contrato em disco continua sendo a fonte
    que o `main.sh` lê. Isto registra no catálogo a versão que se pretende ter
    lá, e a próxima `catalog reconcile` acusa se o disco divergir.
    """
    job = _job_da_instancia(job_id, session, scope)

    relatorio = validate_contract(corpo.contract)
    if not relatorio.valid:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"contrato reprovado no schema: {relatorio.summary()}",
        )

    bruto = json.dumps(corpo.contract, sort_keys=True, ensure_ascii=False).encode()
    contract_hash = hashlib.sha256(bruto).hexdigest()

    existente = session.scalar(
        select(JobContractVersion).where(
            JobContractVersion.job_id == job.id,
            JobContractVersion.contract_hash == contract_hash,
        ).limit(1)
    )
    if existente is not None:
        # Mesmo conteúdo = mesma versão. Republicar não cria história falsa.
        job.current_contract_version_id = existente.id
        session.flush()
        return existente

    ultima = session.scalar(
        select(JobContractVersion.version).where(JobContractVersion.job_id == job.id)
        .order_by(JobContractVersion.version.desc()).limit(1)
    )
    steps = corpo.contract.get("steps") or []
    versao = JobContractVersion(
        job_id=job.id, version=(ultima or 0) + 1,
        schema_version=str(corpo.contract.get("schema_version") or "") or None,
        contract=corpo.contract, contract_hash=contract_hash,
        contract_bytes=len(bruto), steps_count=len(steps) if isinstance(steps, list) else None,
        source="api", validation_status=relatorio.status, validation=relatorio.as_dict(),
        created_by=user.subject,
    )
    session.add(versao)
    session.flush()
    job.current_contract_version_id = versao.id
    job.updated_by = user.subject

    _registrar(session, user=user, action="job.contract.publish", job=job,
               payload={"reason": corpo.reason, "version": versao.version,
                        "contract_hash": contract_hash})
    session.flush()
    return versao
