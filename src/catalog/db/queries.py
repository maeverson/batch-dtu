"""Consultas de leitura do catálogo.

Separado de `repository.py` de propósito: lá é escrita (idempotência,
auditoria, transação), aqui é só leitura. Estas três consultas existem porque
os critérios de aceite do módulo pedem coisas que SQL solto não entrega bem:

* **triagem** — fechar "reconciliação sem divergências não explicadas" exige
  levar os achados a quem decide, agrupados por quem decide, com o
  `fingerprint` que transforma a decisão em `catalog explain`;
* **amostra** — "validação amostral por domínio" precisa ser reproduzível: a
  mesma semente devolve a mesma amostra, senão a conferência de ontem não pode
  ser refeita hoje;
* **histórico** — "versionamento com diffs consultáveis" era SQL manual até
  aqui; sem uma leitura pronta, ninguém consulta.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (
    Job,
    JobConnectionAlias,
    JobContractVersion,
    JobRevision,
    JobSchedule,
    ReconciliationFinding,
    ReconciliationRun,
)

# Achados cuja resposta é decisão de dono de domínio, não conserto de código.
SEVERIDADE_ORDEM = {"erro": 0, "aviso": 1, "info": 2}


@dataclass
class AchadoTriado:
    kind: str
    severity: str
    subject: str
    detail: str
    fingerprint: str
    status: str
    job_process: str | None = None
    job_domain: str | None = None
    job_client: str | None = None
    job_enabled: bool = False
    entrega_a_cliente: bool = False

    @property
    def grupo(self) -> str:
        return f"{self.job_client or '(sem cliente resolvido)'} / {self.job_domain or '-'}"


@dataclass
class Triagem:
    host: str
    run_id: str | None
    achados: list[AchadoTriado] = field(default_factory=list)

    @property
    def por_grupo(self) -> dict[str, list[AchadoTriado]]:
        saida: dict[str, list[AchadoTriado]] = {}
        for a in self.achados:
            saida.setdefault(a.grupo, []).append(a)
        # Grupo com job ativo que entrega a cliente primeiro: é risco corrente,
        # não dívida de catálogo.
        return dict(sorted(
            saida.items(),
            key=lambda kv: (
                not any(a.entrega_a_cliente for a in kv[1]),
                -sum(1 for a in kv[1] if a.job_enabled),
            ),
        ))


def ultima_run(session: Session, host: str) -> ReconciliationRun | None:
    return session.scalar(
        select(ReconciliationRun)
        .where(ReconciliationRun.host == host)
        .order_by(ReconciliationRun.started_at.desc())
        .limit(1)
    )


def triagem(session: Session, host: str, *, apenas_abertos: bool = True) -> Triagem:
    """Achados da última reconciliação do host, ligados ao job que afetam."""
    run = ultima_run(session, host)
    if run is None:
        return Triagem(host=host, run_id=None)

    consulta = (
        select(ReconciliationFinding, Job)
        .outerjoin(Job, Job.id == ReconciliationFinding.job_id)
        .where(ReconciliationFinding.run_id == run.id)
    )
    if apenas_abertos:
        consulta = consulta.where(ReconciliationFinding.status == "open")

    achados: list[AchadoTriado] = []
    for achado, job in session.execute(consulta):
        # O `job_id` só vem preenchido nas divergências estruturais; os achados
        # do build chegam com o sujeito em texto. Resolver pelo nome recupera o
        # vínculo e é o que permite agrupar por cliente.
        alvo = job or _job_por_sujeito(session, host, achado.subject)
        entrega = False
        if alvo is not None and alvo.current_contract_version_id:
            versao = session.get(JobContractVersion, alvo.current_contract_version_id)
            entrega = bool(versao) and any(
                (s or {}).get("function") in ("upload_remote", "download_remote")
                for s in (versao.contract.get("steps") or [])
                if isinstance(s, dict)
            )
        achados.append(AchadoTriado(
            kind=achado.kind, severity=achado.severity, subject=achado.subject,
            detail=achado.detail or "", fingerprint=achado.fingerprint,
            status=achado.status,
            job_process=alvo.process_name if alvo else None,
            job_domain=alvo.domain if alvo else None,
            job_client=(alvo.client_name or alvo.client_code) if alvo else None,
            job_enabled=bool(alvo and alvo.status == "active"),
            entrega_a_cliente=entrega and bool(alvo and alvo.status == "active"),
        ))

    achados.sort(key=lambda a: (SEVERIDADE_ORDEM.get(a.severity, 9), a.kind, a.subject))
    return Triagem(host=host, run_id=str(run.id), achados=achados)


def _job_por_sujeito(session: Session, host: str, sujeito: str) -> Job | None:
    """Resolve o sujeito do achado até o job que ele afeta.

    O sujeito varia por tipo de achado: nome de processo, caminho de contrato ou
    de wrapper, ou **nome de alias**. Sem o último, todo achado de alias cairia
    em "(sem cliente resolvido)" — e é justamente o tipo que mais aparece.
    """
    if not sujeito:
        return None

    alvo = session.scalar(
        select(Job).where(Job.host == host, Job.process_name == sujeito).limit(1)
    )
    if alvo is not None:
        return alvo

    if "/" in sujeito:   # caminho de contrato ou de wrapper
        alvo = session.scalar(
            select(Job)
            .where(Job.host == host,
                   (Job.contract_path == sujeito) | (Job.wrapper_path == sujeito))
            .limit(1)
        )
        if alvo is not None:
            return alvo

    # Alias: o job que o cita. Havendo vários, prefere um ATIVO — é o que define
    # se o achado é risco corrente ou dívida.
    return session.scalar(
        select(Job)
        .join(JobConnectionAlias, JobConnectionAlias.job_id == Job.id)
        .where(Job.host == host, JobConnectionAlias.alias_name == sujeito)
        .order_by((Job.status == "active").desc(), Job.process_name)
        .limit(1)
    )


@dataclass
class ItemAmostra:
    process_name: str
    domain: str | None
    status: str
    status_reason: str | None
    environment: str | None
    client: str | None
    schedules: list[tuple[str | None, bool]] = field(default_factory=list)
    contrato_declara: dict = field(default_factory=dict)


def amostra_por_dominio(
    session: Session, host: str, *, por_dominio: int = 3, seed: int = 20260911
) -> dict[str, list[ItemAmostra]]:
    """Amostra determinística por domínio para conferência humana.

    Determinística porque uma conferência que não pode ser refeita não serve de
    evidência: a mesma semente devolve a mesma amostra, e o registro de quem
    conferiu passa a valer para um conjunto identificável.
    """
    jobs = list(session.scalars(select(Job).where(Job.host == host).order_by(Job.process_name)))
    por_dom: dict[str, list[Job]] = {}
    for job in jobs:
        por_dom.setdefault(job.domain or "(sem dominio)", []).append(job)

    sorteio = random.Random(seed)
    saida: dict[str, list[ItemAmostra]] = {}
    for dominio, lista in sorted(por_dom.items()):
        escolhidos = sorteio.sample(lista, min(por_dominio, len(lista)))
        itens = []
        for job in sorted(escolhidos, key=lambda j: j.process_name):
            agendas = [
                (s.schedule_expr, s.enabled)
                for s in session.scalars(
                    select(JobSchedule).where(JobSchedule.job_id == job.id)
                    .order_by(JobSchedule.cron_lineno)
                )
            ]
            declara = {}
            if job.current_contract_version_id:
                versao = session.get(JobContractVersion, job.current_contract_version_id)
                if versao is not None:
                    declara = {
                        k: versao.contract.get(k)
                        for k in ("client", "country", "environment", "name_process")
                    }
            itens.append(ItemAmostra(
                process_name=job.process_name, domain=job.domain, status=job.status,
                status_reason=job.status_reason, environment=job.environment,
                client=job.client_name or job.client_code,
                schedules=agendas, contrato_declara=declara,
            ))
        saida[dominio] = itens
    return saida


@dataclass
class EventoHistorico:
    tipo: str            # "contrato" | "metadados"
    versao: int
    quando: object
    quem: str
    resumo: str
    detalhe: dict = field(default_factory=dict)


def historico(session: Session, job: Job) -> list[EventoHistorico]:
    """Versões de contrato e revisões de metadados, em ordem cronológica."""
    eventos: list[EventoHistorico] = []

    for versao in session.scalars(
        select(JobContractVersion).where(JobContractVersion.job_id == job.id)
        .order_by(JobContractVersion.version)
    ):
        eventos.append(EventoHistorico(
            tipo="contrato", versao=versao.version, quando=versao.created_at,
            quem=versao.created_by,
            resumo=(f"sha256={versao.contract_hash[:12]} · {versao.steps_count} steps · "
                    f"schema={versao.validation_status}"),
            detalhe={"validation": versao.validation, "source": versao.source},
        ))

    for revisao in session.scalars(
        select(JobRevision).where(JobRevision.job_id == job.id).order_by(JobRevision.version)
    ):
        antes = revisao.diff if isinstance(revisao.diff, dict) else {}
        mudou = ", ".join(sorted(antes)) if antes else "criacao"
        eventos.append(EventoHistorico(
            tipo="metadados", versao=revisao.version, quando=revisao.created_at,
            quem=revisao.created_by, resumo=f"mudou: {mudou}",
            detalhe={"antes": antes, "depois": revisao.snapshot},
        ))

    eventos.sort(key=lambda e: (e.quando, e.tipo, e.versao))
    return eventos


def job_por_nome(session: Session, nome: str, host: str | None = None) -> list[Job]:
    consulta = select(Job).where(Job.process_name == nome)
    if host:
        consulta = consulta.where(Job.host == host)
    return list(session.scalars(consulta.order_by(Job.host)))


def hosts_no_catalogo(session: Session) -> list[str]:
    return list(session.scalars(select(Job.host).group_by(Job.host).order_by(Job.host)))


def contagem_por_host(session: Session) -> dict[str, int]:
    return {
        h: n for h, n in session.execute(
            select(Job.host, func.count()).group_by(Job.host).order_by(Job.host)
        )
    }
