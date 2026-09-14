"""Configuração da Platform API — tudo por variável de ambiente.

Nenhum default aponta para produção: o padrão de cada campo é o serviço
correspondente do `docker-compose.yaml` (Keycloak local, host `legacy`,
Loki do compose). Trocar para Entra ID/produção é só variável de ambiente —
nenhum destes valores deve virar literal em código (`docs/seguranca.md`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class OIDCSettings:
    # Local: Keycloak do compose (`docker compose --profile auth up -d`).
    # Entra ID: `https://login.microsoftonline.com/<tenant>/v2.0`.
    issuer: str = "http://localhost:8080/realms/batch-dtu"
    audience: str = "platform-api"
    jwks_uri: str | None = None   # None = derivado do issuer (OIDC discovery)
    # Claim que carrega as roles. Keycloak: `realm_access.roles`. Entra ID
    # normalmente usa `roles` direto — troca de IdP é mudar só isto.
    roles_claim: str = "realm_access.roles"
    # Claim usada como `subject` de `role_binding` — ver `authz.py` sobre por
    # que NÃO é `sub` (UUID). No Entra ID real, geralmente `preferred_username`
    # vira `upn` ou `unique_name`.
    subject_claim: str = "preferred_username"
    # Tolerância de relógio na validação de exp/iat.
    leeway_seconds: int = 10

    @classmethod
    def from_env(cls) -> "OIDCSettings":
        return cls(
            issuer=os.environ.get("OIDC_ISSUER", cls.issuer),
            audience=os.environ.get("OIDC_AUDIENCE", cls.audience),
            jwks_uri=os.environ.get("OIDC_JWKS_URI") or None,
            roles_claim=os.environ.get("OIDC_ROLES_CLAIM", cls.roles_claim),
            subject_claim=os.environ.get("OIDC_SUBJECT_CLAIM", cls.subject_claim),
            leeway_seconds=int(os.environ.get("OIDC_LEEWAY_SECONDS", cls.leeway_seconds)),
        )


@dataclass(frozen=True)
class SSHBackendSettings:
    """Um host por enquanto (`srv-sftp-2`); a Fase 1 tem dois hosts em escopo
    (ver `CLAUDE.md`), então isto vira mapa host->config antes do primeiro
    reprocesso real em UAT — deixado simples aqui de propósito."""

    host: str = "localhost"
    port: int = 2222
    username: str = "backoffice_svc"
    key_path: str = "docker/legacy/keys/backoffice_svc_ed25519"
    connect_timeout: float = 10.0
    known_hosts: str | None = None   # None = não verifica (dev); nunca em prod

    @classmethod
    def from_env(cls) -> "SSHBackendSettings":
        return cls(
            host=os.environ.get("SSH_BACKEND_HOST", cls.host),
            port=int(os.environ.get("SSH_BACKEND_PORT", cls.port)),
            username=os.environ.get("SSH_BACKEND_USER", cls.username),
            key_path=os.environ.get("SSH_BACKEND_KEY", cls.key_path),
            connect_timeout=float(os.environ.get("SSH_BACKEND_TIMEOUT", cls.connect_timeout)),
            known_hosts=os.environ.get("SSH_BACKEND_KNOWN_HOSTS") or None,
        )


@dataclass(frozen=True)
class LokiSettings:
    base_url: str = "http://localhost:3100"

    @classmethod
    def from_env(cls) -> "LokiSettings":
        return cls(base_url=os.environ.get("LOKI_URL", cls.base_url))


@dataclass(frozen=True)
class Settings:
    oidc: OIDCSettings = field(default_factory=OIDCSettings)
    ssh: SSHBackendSettings = field(default_factory=SSHBackendSettings)
    loki: LokiSettings = field(default_factory=LokiSettings)
    db_role: str = "app"

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            oidc=OIDCSettings.from_env(),
            ssh=SSHBackendSettings.from_env(),
            loki=LokiSettings.from_env(),
            db_role=os.environ.get("PLATFORM_API_DB_ROLE", "app"),
        )
