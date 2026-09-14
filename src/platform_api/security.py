"""Verificação de token OIDC.

Local: Keycloak (`docker compose --profile auth up`), realm `batch-dtu`,
roles `batch.viewer/operator/operator-prod/admin` como **realm roles**
(`realm_access.roles`). Entra ID: troca de `OIDCSettings` (issuer, claim de
roles), nada aqui muda de forma.

**O que este módulo NÃO faz**: não lê escopo de domínio/ambiente do token. A
fixture do Keycloak local declara `batch_domains`/`batch_environments` como
claims — mas a decisão registrada em `modules/platform-api/CLAUDE.md` é que o
escopo mora em `role_binding`, no catálogo, não em claim do IdP. Um IdP audita
identidade; quem audita "pode operar este job" é o mesmo lugar que audita o
job. Essas claims da fixture são ignoradas de propósito — ver `authz.py`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import jwt

from .config import OIDCSettings


class TokenInvalid(Exception):
    """Token ausente, expirado, mal assinado ou sem os claims exigidos."""


@dataclass(frozen=True)
class AuthenticatedUser:
    subject: str                    # chave de `role_binding.subject`
    roles: frozenset[str]
    claims: dict

    def has_role(self, role: str) -> bool:
        return role in self.roles


class SigningKeySource(Protocol):
    """O que `TokenVerifier` precisa do lado do JWKS — só isto, para que o
    teste unitário injete uma chave fixa sem rede."""

    def get_signing_key_from_jwt(self, token: str): ...


def _get_path(claims: dict, dotted: str):
    node = claims
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


class TokenVerifier:
    def __init__(self, settings: OIDCSettings, key_source: SigningKeySource | None = None):
        self.settings = settings
        self._key_source = key_source or jwt.PyJWKClient(
            settings.jwks_uri or f"{settings.issuer}/protocol/openid-connect/certs"
        )

    def verify(self, token: str) -> AuthenticatedUser:
        if not token:
            raise TokenInvalid("token ausente")
        try:
            signing_key = self._key_source.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.settings.audience,
                issuer=self.settings.issuer,
                leeway=self.settings.leeway_seconds,
                options={"require": ["exp", "iat", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise TokenInvalid(str(exc)) from exc

        subject = _get_path(claims, self.settings.subject_claim)
        if not subject:
            raise TokenInvalid(
                f"claim de subject '{self.settings.subject_claim}' ausente ou vazia"
            )

        roles = _get_path(claims, self.settings.roles_claim) or []
        if not isinstance(roles, list):
            raise TokenInvalid(f"claim de roles '{self.settings.roles_claim}' não é lista")

        return AuthenticatedUser(subject=str(subject), roles=frozenset(roles), claims=claims)
