"""New Relic — agente APM no processo e eventos de execução.

A observabilidade da Fase 1 é New Relic (decisão do cliente): é lá que se
acompanha uma execução manual, já que `POST /executions` responde 200 assim
que despacha e **não** espera o `main.sh` terminar (ver
`routers/executions.py`). O que liga as duas pontas é o `execution_id`:

- **Evento**: cada execução emite `BatchExecution` com `execution_id`, job,
  ambiente, host, quem pediu e o desfecho. É a fonte do dashboard "execuções
  manuais" e do alerta de execução que não termina.
- **Log**: o wrapper no host grava `<process>.<execution_id>.log`, e o agente
  de logs do New Relic embarca com `execution_id` como atributo — ver
  `deploy/newrelic/logging.d/` e `newrelic_logs.py`.

O agente é **opcional em import**: o pacote `newrelic` não é dependência do
ambiente de desenvolvimento nem dos testes. Sem ele (ou com
`NEW_RELIC_ENABLED=false`), tudo aqui vira no-op silencioso — nunca derruba
uma execução por causa de telemetria.
"""

from __future__ import annotations

import logging
from typing import Any

from .config import NewRelicSettings

logger = logging.getLogger(__name__)

_agent: Any | None = None


def init_agent(settings: NewRelicSettings) -> bool:
    """Inicializa o agente APM. Idempotente; devolve se ficou ativo.

    Chamado por `cli.py` ANTES de importar/instanciar o app — é como o agente
    instrumenta o ASGI. Em container, o caminho canônico continua sendo
    `newrelic-admin run-program`; esta função é o equivalente embutido, para
    quem roda `platform-api` direto.
    """
    global _agent
    if not settings.enabled:
        return False
    if _agent is not None:
        return True
    try:
        import newrelic.agent as agent
    except ImportError:
        logger.warning(
            "NEW_RELIC_ENABLED=true mas o pacote 'newrelic' não está instalado "
            "(pip install '.[newrelic]') — seguindo sem APM"
        )
        return False
    agent.initialize(settings.config_file) if settings.config_file else agent.initialize()
    _agent = agent
    logger.info("New Relic APM ativo (app_name=%s)", settings.app_name)
    return True


def record_execution_event(payload: dict) -> None:
    """Emite `BatchExecution`. Nunca levanta: telemetria não derruba execução."""
    if _agent is None:
        return
    try:
        _agent.record_custom_event("BatchExecution", payload)
    except Exception:                                   # noqa: BLE001
        logger.exception("falha ao emitir evento BatchExecution (ignorada)")


def add_execution_context(execution_id: str, job: Any, environment: str) -> None:
    """Carimba a transação atual com o `execution_id`, para o trace do APM
    cruzar com o log e com o evento sem ninguém procurar por timestamp."""
    if _agent is None:
        return
    try:
        _agent.add_custom_attributes([
            ("execution_id", execution_id),
            ("batch.process_name", getattr(job, "process_name", None)),
            ("batch.domain", getattr(job, "domain", None)),
            ("batch.environment", environment),
        ])
    except Exception:                                   # noqa: BLE001
        logger.exception("falha ao anotar atributos no APM (ignorada)")
