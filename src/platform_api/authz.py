"""Resolução de escopo — `role_binding`, não claim de token.

O token do IdP prova **identidade** e a classe de role que a organização
concedeu (`realm_access.roles` / Entra App Roles). Ele NÃO prova em que
domínio/ambiente/host essa role vale — isso é o que `role_binding` audita, e é
por isso que toda checagem aqui exige uma LINHA na tabela, nunca só a
presença da role no token. Sem essa linha, a role é uma entitlement genérica
sem escopo concreto, e não autoriza nada.

`NULL` numa dimensão do binding = essa dimensão não restringe (todas as
domínio/ambiente/host). Conceder um binding sempre restringe a partir de
"tudo"; nunca amplia além do que a role do token já permite.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from catalog.db.models import Job, RoleBinding

from .security import AuthenticatedUser

# Ambiente cujo escopo de execução/escrita exige `batch.operator-prod`. Todo
# outro ambiente (UAT/TEST/DEV) aceita `batch.operator`. Reflete
# docs/seguranca.md: "`batch.operator-prod`: Operar em PROD".
_PROD_ENVIRONMENTS = frozenset({"PROD"})


def required_operate_role(environment: str | None) -> str:
    return "batch.operator-prod" if environment in _PROD_ENVIRONMENTS else "batch.operator"


def active_bindings(session: Session, subject: str) -> list[RoleBinding]:
    return list(
        session.scalars(
            select(RoleBinding).where(
                RoleBinding.subject == subject, RoleBinding.revoked_at.is_(None)
            )
        )
    )


def binding_matches(binding: RoleBinding, job: Job) -> bool:
    """Público de propósito: routers/jobs.py reaproveita para o filtro de
    listagem — duplicar esta regra em SQL seria uma segunda fonte de verdade."""
    if binding.scope_domain is not None and binding.scope_domain != job.domain:
        return False
    if binding.scope_environment is not None and binding.scope_environment != job.environment:
        return False
    if binding.scope_host is not None and binding.scope_host != job.host:
        return False
    return True


_matches = binding_matches  # nome curto para uso interno neste módulo


@dataclass(frozen=True)
class Scope:
    """Escopo resolvido de um usuário — bindings já carregados do banco."""

    subject: str
    token_roles: frozenset[str]
    bindings: tuple[RoleBinding, ...]

    def can_view(self, job: Job) -> bool:
        """Qualquer binding ativo, de qualquer role, dá visibilidade — ver o
        job é o piso de toda role, inclusive `batch.viewer`."""
        return any(_matches(b, job) for b in self.bindings)

    def can_operate(self, job: Job) -> bool:
        """Execução manual, enable/disable: precisa da role adequada ao
        ambiente do job (PROD exige operator-prod) NO TOKEN, e um binding
        concreto — de `batch.admin` ou da role exigida — cobrindo esse job."""
        needed = required_operate_role(job.environment)
        if needed not in self.token_roles and "batch.admin" not in self.token_roles:
            return False
        return any(
            _matches(b, job) and b.role in (needed, "batch.admin") for b in self.bindings
        )

    def is_admin_for(self, job: Job) -> bool:
        return "batch.admin" in self.token_roles and any(
            _matches(b, job) and b.role == "batch.admin" for b in self.bindings
        )

    def visible_domains(self) -> set[str] | None:
        """None = todos os domínios (algum binding sem `scope_domain`)."""
        if any(b.scope_domain is None for b in self.bindings):
            return None
        return {b.scope_domain for b in self.bindings if b.scope_domain}

    def visible_environments(self) -> set[str] | None:
        if any(b.scope_environment is None for b in self.bindings):
            return None
        return {b.scope_environment for b in self.bindings if b.scope_environment}


def resolve_scope(session: Session, user: AuthenticatedUser) -> Scope:
    return Scope(
        subject=user.subject,
        token_roles=user.roles,
        bindings=tuple(active_bindings(session, user.subject)),
    )


class NotAuthorized(Exception):
    """Token válido, mas sem escopo para a ação — vira 403, nunca 401."""

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


def require_view(scope: Scope, job: Job) -> None:
    if not scope.can_view(job):
        raise NotAuthorized(
            f"sujeito '{scope.subject}' sem role_binding cobrindo "
            f"{job.host}/{job.domain}/{job.environment}"
        )


def require_operate(scope: Scope, job: Job) -> None:
    if not scope.can_operate(job):
        needed = required_operate_role(job.environment)
        raise NotAuthorized(
            f"operar {job.process_name} ({job.environment}) exige '{needed}' "
            f"com role_binding cobrindo {job.host}/{job.domain}/{job.environment}"
        )
