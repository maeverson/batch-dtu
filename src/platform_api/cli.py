"""`platform-api` — sobe o serviço com uvicorn, configuração 100% por ambiente.

As variáveis sobem **junto com o serviço** (env do container, `env_file` de
`deploy/platform-api/`), nunca exportadas no shell do host: ver
`deploy/README.md`. Para desenvolvimento contra o compose:

    docker compose --profile auth --profile legacy up -d
    set -a; . deploy/platform-api/local.env; set +a
    platform-api
"""

from __future__ import annotations

import os
import sys


def main() -> None:
    import uvicorn

    from .config import ConfigurationError, Settings
    from .telemetry import init_agent

    try:
        settings = Settings.from_env()
        settings.validate()
    except ConfigurationError as exc:
        # Sem stack trace: quem lê isto no log do pod precisa da variável que
        # falta, não do caminho do código que reclamou.
        print(f"[platform-api] configuração inválida: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    # Antes de instanciar o app: é assim que o agente instrumenta o ASGI.
    init_agent(settings.newrelic)

    print(
        f"[platform-api] ambiente={settings.environment} host={settings.host} "
        f"ssh={settings.ssh.username}@{settings.ssh.host}:{settings.ssh.port} "
        f"logs={settings.log_backend} apm={'on' if settings.newrelic.enabled else 'off'}",
        file=sys.stderr,
    )

    uvicorn.run(
        "platform_api.app:create_app",
        factory=True,
        host=os.environ.get("PLATFORM_API_HOST", "0.0.0.0"),
        port=int(os.environ.get("PLATFORM_API_PORT", "8000")),
        reload=os.environ.get("PLATFORM_API_RELOAD", "").lower() in ("1", "true"),
    )


if __name__ == "__main__":
    main()
