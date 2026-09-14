"""`platform-api` — sobe o serviço com uvicorn, configuração 100% por ambiente
(ver `config.py`). Para desenvolvimento contra o compose:

    docker compose --profile auth --profile legacy up -d
    export DATABASE_URL=postgresql+psycopg://batch_app:batch_app_dev@localhost:5432/batch_catalog
    platform-api
"""

from __future__ import annotations

import os


def main() -> None:
    import uvicorn

    uvicorn.run(
        "platform_api.app:create_app",
        factory=True,
        host=os.environ.get("PLATFORM_API_HOST", "0.0.0.0"),
        port=int(os.environ.get("PLATFORM_API_PORT", "8000")),
        reload=os.environ.get("PLATFORM_API_RELOAD", "").lower() in ("1", "true"),
    )


if __name__ == "__main__":
    main()
