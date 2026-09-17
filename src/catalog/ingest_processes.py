"""Carga do catálogo a partir da ÁRVORE DE CONTRATOS, direto no RDS.

Complementa — não substitui — `catalog load`. A diferença está na fonte:

| | fonte | traz |
|---|---|---|
| `catalog load` | pacote do coletor | job, **agenda**, contrato, crontab, reconciliação |
| `catalog ingest-processes` | um diretório de contratos | job + contrato |

Este módulo existe para o caso em que se tem o diretório de contratos e não um
pacote de coleta — por exemplo `/opt2/batch_v2/batch-commons-framework/processes/base2`,
montado ou copiado. **Cada `.json` é o contrato de um job**, e o nome do
arquivo é o `process_name`.

O que ele deliberadamente NÃO faz, para não mentir sobre o que sabe:

- **não inventa agenda.** Sem o crontab não há como saber quando o job roda; o
  job entra com `status='on_demand'` (a menos que `--status` diga outra coisa)
  e nenhuma `job_schedule` é criada. Um job "ativo" sem agenda registrada
  aparece na reconciliação como divergência, e estaria certo.
- **não apaga nada.** Contrato que sumiu do diretório não vira job removido —
  isso é trabalho de `catalog reconcile`, que compara contra uma coleta
  completa. É relatado como `ausente_no_diretorio` e nada mais.

Idempotente: rodar duas vezes não duplica job nem versão de contrato (a versão
é append-only por hash do conteúdo, como em `db/repository.py`).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .contract_schema import validate_contract
from .db.models import AuditEvent, Job, JobContractVersion, JobRevision

# Raiz do framework no host — o `contract_path` gravado no catálogo precisa ser
# o caminho COMO O HOST O VÊ, porque é ele que vai para `--process-file` na
# execução. Ler de uma cópia local não pode mudar esse caminho.
DEFAULT_FRAMEWORK_ROOT = "/opt2/batch_v2/batch-commons-framework"


@dataclass
class IngestReport:
    host: str
    environment: str
    domain: str
    contract_root: str
    dry_run: bool = False
    files_read: int = 0
    invalid_json: list[str] = field(default_factory=list)
    schema_invalid: list[str] = field(default_factory=list)
    jobs_created: int = 0
    jobs_updated: int = 0
    jobs_unchanged: int = 0
    versions_created: int = 0
    versions_reused: int = 0
    audit_events: int = 0
    missing_in_directory: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.invalid_json


def _hash_contrato(bruto: bytes) -> str:
    return hashlib.sha256(bruto).hexdigest()


def _cliente_do_contrato(contrato: dict) -> tuple[str | None, str | None]:
    """`client` é o campo que o parque usa; `client_name` só quando o contrato
    o traz explicitamente. Nunca adivinhar cliente a partir do nome do arquivo
    — cliente errado num `upload_remote` é arquivo no destino errado."""
    codigo = contrato.get("client")
    nome = contrato.get("client_name")
    return (str(codigo) if codigo else None, str(nome) if nome else None)


def _paises(contrato: dict) -> list[str]:
    valor = contrato.get("country") or contrato.get("countries")
    if isinstance(valor, str):
        return [valor.upper()]
    if isinstance(valor, list):
        return [str(v).upper() for v in valor if v]
    return []


def _audit(session: Session, report: IngestReport, **kwargs) -> None:
    session.add(AuditEvent(source="cli", occurred_at=datetime.now(timezone.utc), **kwargs))
    report.audit_events += 1


def ingest_directory(
    directory: Path,
    session: Session,
    *,
    host: str,
    environment: str,
    domain: str | None = None,
    actor: str = "cli",
    framework_root: str = DEFAULT_FRAMEWORK_ROOT,
    status: str = "on_demand",
    dry_run: bool = False,
) -> IngestReport:
    """Lê `directory/*.json` e persiste job + versão de contrato.

    `framework_root` + `domain` formam o `contract_path` gravado — o caminho no
    HOST, mesmo quando se lê de uma cópia local.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise NotADirectoryError(f"diretório de contratos inexistente: {directory}")

    domain = domain or directory.name
    contract_root = f"{framework_root.rstrip('/')}/processes/{domain}"
    report = IngestReport(
        host=host, environment=environment, domain=domain,
        contract_root=contract_root, dry_run=dry_run,
    )

    vistos: set[str] = set()
    for arquivo in sorted(directory.glob("*.json")):
        report.files_read += 1
        bruto = arquivo.read_bytes()
        try:
            contrato = json.loads(bruto)
        except json.JSONDecodeError as exc:
            # Contrato quebrado NÃO entra no catálogo: o catálogo mostra o
            # parque como ele é, mas um JSON que nem parseia não tem "como ele
            # é" — é ruído que viraria job fantasma.
            report.invalid_json.append(f"{arquivo.name}: {exc}")
            continue
        if not isinstance(contrato, dict):
            report.invalid_json.append(f"{arquivo.name}: raiz do JSON não é objeto")
            continue

        process_name = arquivo.stem
        contract_path = f"{contract_root}/{arquivo.name}"
        vistos.add(contract_path)

        relatorio = validate_contract(contrato)
        if not relatorio.valid:
            report.schema_invalid.append(f"{arquivo.name}: {relatorio.summary()}")

        codigo_cliente, nome_cliente = _cliente_do_contrato(contrato)
        job = session.scalar(
            select(Job).where(Job.host == host, Job.contract_path == contract_path).limit(1)
        )

        if job is None:
            job = Job(
                host=host, environment=environment, process_name=process_name,
                contract_path=contract_path, domain=domain,
                client_code=codigo_cliente, client_name=nome_cliente,
                country_codes=_paises(contrato), kind="contract_job",
                status=status, created_by=actor, updated_by=actor,
            )
            session.add(job)
            session.flush()
            report.jobs_created += 1
            _audit(session, report, actor=actor, action="job.create", target_type="job",
                   target_id=str(job.id),
                   payload={"origem": "ingest-processes", "contract_path": contract_path,
                            "process_name": process_name, "environment": environment})
        else:
            campos = {
                "process_name": process_name, "domain": domain,
                "client_code": codigo_cliente or job.client_code,
                "client_name": nome_cliente or job.client_name,
                "environment": environment,
            }
            antes = {k: getattr(job, k) for k, v in campos.items() if getattr(job, k) != v}
            if antes:
                for campo, valor in campos.items():
                    setattr(job, campo, valor)
                job.updated_by = actor
                ultima = session.scalar(
                    select(JobRevision.version).where(JobRevision.job_id == job.id)
                    .order_by(JobRevision.version.desc()).limit(1)
                )
                session.add(JobRevision(
                    job_id=job.id, version=(ultima or 0) + 1,
                    snapshot=campos, diff=antes, created_by=actor,
                ))
                report.jobs_updated += 1
                _audit(session, report, actor=actor, action="job.update", target_type="job",
                       target_id=str(job.id),
                       payload={"origem": "ingest-processes", "before": antes, "after": campos})
            else:
                report.jobs_unchanged += 1

        _persistir_versao(
            session, report, job=job, contrato=contrato, bruto=bruto,
            relatorio=relatorio, actor=actor,
        )

    # Contrato que existe no catálogo e não está mais no diretório: relatado,
    # nunca apagado (ver docstring do módulo).
    no_catalogo = session.scalars(
        select(Job.contract_path).where(
            Job.host == host, Job.contract_path.like(f"{contract_root}/%")
        )
    )
    report.missing_in_directory = sorted(
        caminho for caminho in no_catalogo if caminho and caminho not in vistos
    )

    if dry_run:
        session.rollback()
    return report


def _persistir_versao(session: Session, report: IngestReport, *, job: Job, contrato: dict,
                      bruto: bytes, relatorio, actor: str) -> None:
    contract_hash = _hash_contrato(bruto)
    ja_existe = session.scalar(
        select(JobContractVersion).where(
            JobContractVersion.job_id == job.id,
            JobContractVersion.contract_hash == contract_hash,
        ).limit(1)
    )
    if ja_existe is not None:
        if job.current_contract_version_id != ja_existe.id:
            job.current_contract_version_id = ja_existe.id
        report.versions_reused += 1
        return

    ultima = session.scalar(
        select(JobContractVersion.version).where(JobContractVersion.job_id == job.id)
        .order_by(JobContractVersion.version.desc()).limit(1)
    )
    steps = contrato.get("steps") or []
    versao = JobContractVersion(
        job_id=job.id, version=(ultima or 0) + 1,
        schema_version=str(contrato.get("schema_version") or "") or None,
        contract=contrato, contract_hash=contract_hash, contract_bytes=len(bruto),
        steps_count=len(steps) if isinstance(steps, list) else None,
        # `source` é o CHECK de `job_contract_version`: 'import' é o valor que
        # descreve "veio de fora, por carga", que é o que isto é. Não vale
        # inventar um valor novo sem migration.
        source="import", created_by=actor,
        validation_status=relatorio.status, validation=relatorio.as_dict(),
    )
    session.add(versao)
    session.flush()
    job.current_contract_version_id = versao.id
    report.versions_created += 1
    _audit(session, report, actor=actor, action="job.contract_version", target_type="job",
           target_id=str(job.id),
           payload={"origem": "ingest-processes", "version": versao.version,
                    "contract_hash": contract_hash,
                    "validation_status": versao.validation_status})
