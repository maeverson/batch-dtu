"""Configuração da Platform API — tudo por variável de ambiente.

Nenhum default aponta para produção: o padrão de cada campo é o serviço
correspondente do `docker-compose.yaml` (host `legacy`, Keycloak local de
desenvolvimento). Em qualquer ambiente implantado as variáveis **sobem junto
com o serviço** (env do container / `env_file` por módulo em `deploy/`), nunca
exportadas no perfil do shell do host.

Três decisões do cliente estão codificadas aqui:

- **Uma instância serve UM ambiente e UM host** (`PLATFORM_ENVIRONMENT`,
  `PLATFORM_HOST`). UAT e PROD são deploys distintos da mesma imagem, com
  variáveis distintas. É `deployment_scope()` quem transforma isso em filtro.
- **RBAC vem inteiro do Entra ID** — as roles do token são a autorização;
  não há tabela de escopo consultada em runtime (ver `authz.py`).
- **Observabilidade é New Relic** — agente APM no processo e logs consultados
  por NerdGraph (`newrelic_logs.py`). Loki continua suportado só para o
  ambiente local do `docker-compose.yaml`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


class ConfigurationError(RuntimeError):
    """Configuração incompleta ou insegura — o processo não sobe.

    Falhar no start é deliberado: uma API que sobe sem verificação de chave de
    host, ou sem saber que ambiente serve, é pior que uma API que não sobe.
    """


def _flag(nome: str, default: bool = False) -> bool:
    valor = os.environ.get(nome)
    if valor is None:
        return default
    return valor.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class OIDCSettings:
    """Entra ID em qualquer ambiente implantado; Keycloak só no compose local.

    Entra (token v2, `accessTokenAcceptedVersion: 2`):
        OIDC_ISSUER=https://login.microsoftonline.com/<tenant>/v2.0
        OIDC_AUDIENCE=<client-id da app registration da API>
        OIDC_JWKS_URI=https://login.microsoftonline.com/<tenant>/discovery/v2.0/keys
        OIDC_ROLES_CLAIM=roles
        OIDC_SUBJECT_CLAIM=oid
    """

    issuer: str = "http://localhost:8080/realms/batch-dtu"
    audience: str = "platform-api"
    jwks_uri: str | None = None   # None = derivado do issuer (formato Keycloak)
    roles_claim: str = "realm_access.roles"
    # Claim usada como identidade na trilha de auditoria. No Entra, `oid` é o
    # GUID imutável do usuário no tenant: `preferred_username`/`upn` mudam com
    # casamento, troca de sobrenome ou migração de domínio de e-mail, e a
    # trilha passaria a apontar para alguém que "não existe".
    subject_claim: str = "preferred_username"
    # Claim opcional com um nome legível, gravado JUNTO do subject na
    # auditoria. Sem isto, `oid` deixa a trilha ilegível para quem audita.
    display_name_claim: str = "preferred_username"
    leeway_seconds: int = 10

    @classmethod
    def from_env(cls) -> "OIDCSettings":
        return cls(
            issuer=os.environ.get("OIDC_ISSUER", cls.issuer),
            audience=os.environ.get("OIDC_AUDIENCE", cls.audience),
            jwks_uri=os.environ.get("OIDC_JWKS_URI") or None,
            roles_claim=os.environ.get("OIDC_ROLES_CLAIM", cls.roles_claim),
            subject_claim=os.environ.get("OIDC_SUBJECT_CLAIM", cls.subject_claim),
            display_name_claim=os.environ.get(
                "OIDC_DISPLAY_NAME_CLAIM", cls.display_name_claim
            ),
            leeway_seconds=int(os.environ.get("OIDC_LEEWAY_SECONDS", cls.leeway_seconds)),
        )


@dataclass(frozen=True)
class SSHBackendSettings:
    """O host do `main.sh` que ESTA instância opera.

    Um host por instância, por decisão do cliente (G1): UAT e PROD são deploys
    separados. Não existe roteamento por job — se o job não é deste host, esta
    instância não o serve (ver `authz.deployment_scope`).

    A relação de confiança é chave pública + `known_hosts`:
    `backoffice_svc` no host tem a pública em `authorized_keys` restrito por
    `command=`, e esta ponta verifica a chave do host contra `known_hosts`.
    Agnóstico de ambiente: os mesmos campos valem para UAT e PROD, só mudam
    de valor.
    """

    host: str = "localhost"
    port: int = 2222
    username: str = "backoffice_svc"
    key_path: str = "docker/legacy/keys/backoffice_svc_ed25519"
    connect_timeout: float = 10.0
    known_hosts: str | None = None
    # Único caminho para rodar SEM verificar a chave do host. Existe para o
    # `docker-compose.yaml` local, onde a chave do container muda a cada
    # rebuild. Ligá-lo num ambiente implantado é abrir MITM na rede interna.
    allow_unknown_hosts: bool = False

    @classmethod
    def from_env(cls) -> "SSHBackendSettings":
        return cls(
            host=os.environ.get("SSH_BACKEND_HOST", cls.host),
            port=int(os.environ.get("SSH_BACKEND_PORT", cls.port)),
            username=os.environ.get("SSH_BACKEND_USER", cls.username),
            key_path=os.environ.get("SSH_BACKEND_KEY", cls.key_path),
            connect_timeout=float(os.environ.get("SSH_BACKEND_TIMEOUT", cls.connect_timeout)),
            known_hosts=os.environ.get("SSH_BACKEND_KNOWN_HOSTS") or None,
            allow_unknown_hosts=_flag("SSH_BACKEND_ALLOW_UNKNOWN_HOSTS"),
        )

    def validate(self) -> None:
        if self.known_hosts:
            if not os.path.isfile(self.known_hosts):
                raise ConfigurationError(
                    f"SSH_BACKEND_KNOWN_HOSTS aponta para arquivo inexistente: "
                    f"{self.known_hosts}"
                )
            return
        if not self.allow_unknown_hosts:
            raise ConfigurationError(
                "SSH_BACKEND_KNOWN_HOSTS não definido. A relação de confiança com o "
                "host do main.sh exige verificação da chave do host. Para o ambiente "
                "local do docker-compose, defina SSH_BACKEND_ALLOW_UNKNOWN_HOSTS=true "
                "— nunca em UAT ou PROD."
            )


@dataclass(frozen=True)
class NewRelicSettings:
    """Observabilidade da Fase 1: APM no processo + logs por NerdGraph.

    `license_key` é do agente APM (ingestão); `api_key` é uma User Key, usada
    só para CONSULTAR log por `execution_id` em `GET /executions/{id}/logs`.
    São chaves diferentes de propósito — a de consulta não pode ingerir e a de
    ingestão não pode ler.
    """

    enabled: bool = False
    app_name: str = "batch-dtu-platform-api"
    license_key: str | None = None
    account_id: str | None = None
    api_key: str | None = None
    nerdgraph_url: str = "https://api.newrelic.com/graphql"
    # Conta na região EU usa api.eu.newrelic.com — mesma variável, outro valor.
    config_file: str | None = None

    @classmethod
    def from_env(cls) -> "NewRelicSettings":
        return cls(
            enabled=_flag("NEW_RELIC_ENABLED"),
            app_name=os.environ.get("NEW_RELIC_APP_NAME", cls.app_name),
            license_key=os.environ.get("NEW_RELIC_LICENSE_KEY") or None,
            account_id=os.environ.get("NEW_RELIC_ACCOUNT_ID") or None,
            api_key=os.environ.get("NEW_RELIC_API_KEY") or None,
            nerdgraph_url=os.environ.get("NEW_RELIC_NERDGRAPH_URL", cls.nerdgraph_url),
            config_file=os.environ.get("NEW_RELIC_CONFIG_FILE") or None,
        )

    @property
    def can_query_logs(self) -> bool:
        return bool(self.account_id and self.api_key)

    def validate(self) -> None:
        if self.enabled and not self.license_key:
            raise ConfigurationError(
                "NEW_RELIC_ENABLED=true exige NEW_RELIC_LICENSE_KEY (chave de ingestão "
                "do agente APM)."
            )


@dataclass(frozen=True)
class LokiSettings:
    """Só para o ambiente local do compose — em UAT/PROD o backend é New Relic."""

    base_url: str = "http://localhost:3100"

    @classmethod
    def from_env(cls) -> "LokiSettings":
        return cls(base_url=os.environ.get("LOKI_URL", cls.base_url))


_DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://localhost:3001"

# Ambientes que exigem `batch.operator-prod` para operar (docs/seguranca.md).
PROD_ENVIRONMENTS = frozenset({"PROD"})


@dataclass(frozen=True)
class Settings:
    # --- Escopo do deploy (G1: uma instância, um ambiente, um host) ---------
    # `environment` casa com `job.environment` no catálogo (PROD/UAT/TEST/DEV).
    # `host` casa com `job.host` — é o HOSTNAME do catálogo
    # (`com-ins-bch-mdw-dtu-1`, `srv-sftp-2`), não o IP do SSH.
    environment: str = "UAT"
    host: str = "com-ins-bch-mdw-dtu-1"

    oidc: OIDCSettings = field(default_factory=OIDCSettings)
    ssh: SSHBackendSettings = field(default_factory=SSHBackendSettings)
    newrelic: NewRelicSettings = field(default_factory=NewRelicSettings)
    loki: LokiSettings = field(default_factory=LokiSettings)
    # 'newrelic' | 'loki' — de onde `GET /executions/{id}/logs` lê.
    log_backend: str = "loki"
    db_role: str = "app"
    cors_allow_origins: tuple[str, ...] = tuple(_DEFAULT_CORS_ORIGINS.split(","))

    @classmethod
    def from_env(cls) -> "Settings":
        origens = os.environ.get("CORS_ALLOW_ORIGINS", _DEFAULT_CORS_ORIGINS)
        return cls(
            environment=os.environ.get("PLATFORM_ENVIRONMENT", cls.environment).upper(),
            host=os.environ.get("PLATFORM_HOST", cls.host),
            oidc=OIDCSettings.from_env(),
            ssh=SSHBackendSettings.from_env(),
            newrelic=NewRelicSettings.from_env(),
            loki=LokiSettings.from_env(),
            log_backend=os.environ.get("LOG_BACKEND", cls.log_backend).lower(),
            db_role=os.environ.get("PLATFORM_API_DB_ROLE", "app"),
            cors_allow_origins=tuple(o.strip() for o in origens.split(",") if o.strip()),
        )

    @property
    def is_prod(self) -> bool:
        return self.environment in PROD_ENVIRONMENTS

    def validate(self) -> None:
        """Chamado por `create_app` — falha de configuração aparece no start,
        não na primeira execução manual de um operador."""
        if not self.environment:
            raise ConfigurationError("PLATFORM_ENVIRONMENT é obrigatório (UAT, PROD, TEST, DEV)")
        if not self.host:
            raise ConfigurationError(
                "PLATFORM_HOST é obrigatório — o hostname do catálogo que esta "
                "instância serve (ex.: com-ins-bch-mdw-dtu-1)"
            )
        if self.log_backend not in ("newrelic", "loki"):
            raise ConfigurationError("LOG_BACKEND deve ser 'newrelic' ou 'loki'")
        if self.log_backend == "newrelic" and not self.newrelic.can_query_logs:
            raise ConfigurationError(
                "LOG_BACKEND=newrelic exige NEW_RELIC_ACCOUNT_ID e NEW_RELIC_API_KEY "
                "(User Key, só de consulta)."
            )
        self.ssh.validate()
        self.newrelic.validate()
