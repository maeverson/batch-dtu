"""Verificação de token OIDC.

Ambiente implantado: **Entra ID**, token v2
(`accessTokenAcceptedVersion: 2`), app roles em `roles`. Ambiente local de
desenvolvimento: Keycloak do `docker-compose.yaml`, realm roles em
`realm_access.roles`. A diferença é inteira em `OIDCSettings` — nada aqui
muda de forma.

As roles do token **são** a autorização (decisão do cliente: RBAC no Entra).
`authz.py` não consulta mais tabela nenhuma para decidir; o que restringe
ambiente/host é o deploy, não uma linha de banco.

`display_name` é lido à parte do `subject` porque a identidade estável do
Entra (`oid`) é um GUID: gravar só ele deixa a trilha de auditoria ilegível
para quem a lê depois.
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
    subject: str                    # identidade estável (Entra: `oid`)
    roles: frozenset[str]
    claims: dict
    display_name: str | None = None  # legível na auditoria (Entra: `preferred_username`)

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

        nome = _get_path(claims, self.settings.display_name_claim)
        return AuthenticatedUser(
            subject=str(subject),
            roles=frozenset(roles),
            claims=claims,
            display_name=str(nome) if nome else None,
        )
