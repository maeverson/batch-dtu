"""Autorização — **as roles do Entra ID são a única fonte** (decisão do cliente).

O que mudou em relação ao desenho anterior: não existe mais consulta a
`role_binding` em runtime. O token do Entra prova identidade E entitlement, e
a dimensão que antes o binding restringia (ambiente/host) agora é resolvida
pelo DEPLOY: cada instância serve um ambiente e um host
(`PLATFORM_ENVIRONMENT`/`PLATFORM_HOST` em `config.py`). Operar PROD e operar
UAT são instâncias distintas, com app registrations e grupos distintos no
Entra.

Consequência honesta desse desenho, para quem for auditar: **não há mais
escopo por domínio**. Um `batch.operator` alcança todo job do ambiente daquela
instância. Se a operação precisar de "operador só de /reportes", isso volta a
exigir uma dimensão que o Entra não carrega hoje — ou app roles por domínio,
ou a tabela de binding de volta. A tabela `role_binding` continua existindo no
schema (migration aplicada), mas **nenhum código a lê**.

Mapa de roles (`docs/seguranca.md`), exatamente como o `value` da app role:

| role | pode |
|---|---|
| `batch.viewer` | ver catálogo, execuções e logs |
| `batch.operator` | + executar/reprocessar em ambiente não-PROD |
| `batch.operator-prod` | + executar/reprocessar em PROD |
| `batch.admin` | + CRUD de catálogo e leitura de auditoria |
"""

from __future__ import annotations

from dataclasses import dataclass

from catalog.db.models import Job

from .config import PROD_ENVIRONMENTS, Settings
from .security import AuthenticatedUser

VIEWER = "batch.viewer"
OPERATOR = "batch.operator"
OPERATOR_PROD = "batch.operator-prod"
ADMIN = "batch.admin"

ALL_ROLES = frozenset({VIEWER, OPERATOR, OPERATOR_PROD, ADMIN})


def required_operate_role(environment: str | None) -> str:
    return OPERATOR_PROD if environment in PROD_ENVIRONMENTS else OPERATOR


class NotAuthorized(Exception):
    """Token válido, mas sem role para a ação — vira 403, nunca 401."""

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class Scope:
    """Escopo efetivo: roles do token × ambiente/host desta instância."""

    subject: str
    display_name: str | None
    token_roles: frozenset[str]
    environment: str
    host: str

    # -- pertinência ao deploy ------------------------------------------------
    def serves(self, job: Job) -> bool:
        """O job é deste deploy? Um job de PROD nunca é servido pela instância
        de UAT, nem com `batch.admin` — a separação é do deploy, não da role,
        e é ela que garante que o canal SSH desta instância só alcança o host
        que ela declara operar."""
        return job.environment == self.environment and job.host == self.host

    # -- decisões -------------------------------------------------------------
    @property
    def is_admin(self) -> bool:
        return ADMIN in self.token_roles

    def can_view(self, job: Job) -> bool:
        return self.serves(job) and bool(self.token_roles & ALL_ROLES)

    def can_operate(self, job: Job) -> bool:
        if not self.serves(job):
            return False
        if self.is_admin:
            return True
        return required_operate_role(job.environment) in self.token_roles

    # -- para a UI (`GET /me`) ------------------------------------------------
    def visible_environments(self) -> list[str]:
        return [self.environment]

    def visible_hosts(self) -> list[str]:
        return [self.host]


def resolve_scope(user: AuthenticatedUser, settings: Settings) -> Scope:
    return Scope(
        subject=user.subject,
        display_name=user.display_name,
        token_roles=user.roles,
        environment=settings.environment,
        host=settings.host,
    )


def require_view(scope: Scope, job: Job) -> None:
    if not scope.serves(job):
        raise NotAuthorized(
            f"job de {job.host}/{job.environment} não é servido por esta instância "
            f"({scope.host}/{scope.environment})"
        )
    if not scope.can_view(job):
        raise NotAuthorized(
            f"'{scope.subject}' não tem nenhuma role batch.* no token do Entra ID"
        )


def require_operate(scope: Scope, job: Job) -> None:
    if not scope.serves(job):
        raise NotAuthorized(
            f"job de {job.host}/{job.environment} não é servido por esta instância "
            f"({scope.host}/{scope.environment})"
        )
    if not scope.can_operate(job):
        raise NotAuthorized(
            f"operar {job.process_name} ({job.environment}) exige a app role "
            f"'{required_operate_role(job.environment)}' no Entra ID"
        )


def require_admin(scope: Scope) -> None:
    if not scope.is_admin:
        raise NotAuthorized(f"esta ação exige a app role '{ADMIN}' no Entra ID")
