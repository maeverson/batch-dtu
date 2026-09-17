"""Consulta de log por `execution_id` no New Relic (NerdGraph/NRQL).

É o outro lado de `deploy/newrelic/logging.d/batch-backoffice.yml`: o agente
no host embarca `logs/backoffice/<domínio>/<processo>.<execution_id>.log` e a
regra de parsing promove `execution_id` a atributo. Aqui a API pergunta por
esse atributo — a mesma correlação que o backend Loki fazia por label, sem
mudar o contrato de `GET /executions/{id}/logs`.

A chave usada é uma **User Key** (`NEW_RELIC_API_KEY`), que só consulta.
Nunca a license key de ingestão.
"""

from __future__ import annotations

from datetime import datetime

import httpx

_NERDGRAPH_QUERY = """
query($accountId: Int!, $nrql: Nrql!) {
  actor {
    account(id: $accountId) {
      nrql(query: $nrql) { results }
    }
  }
}
"""


class NewRelicLogsUnavailable(RuntimeError):
    """New Relic indisponível ou recusou a consulta — vira 502, nunca 500."""


def _nrql(execution_id: str, inicio: datetime, fim: datetime, limite: int) -> str:
    # `SINCE ... UNTIL` com epoch em ms: o relógio do host executor não é o
    # mesmo deste processo, e a janela já vem alargada por quem chama.
    desde = int(inicio.timestamp() * 1000)
    ate = int(fim.timestamp() * 1000)
    return (
        "SELECT timestamp, message FROM Log "
        f"WHERE execution_id = '{execution_id}' "
        f"SINCE {desde} UNTIL {ate} "
        f"ORDER BY timestamp ASC LIMIT {limite}"
    )


async def fetch_logs(
    *, account_id: str, api_key: str, nerdgraph_url: str,
    execution_id: str, inicio: datetime, fim: datetime, limite: int = 1000,
) -> list[dict]:
    """Devolve `[{"timestamp_ns": str, "line": str}]` — a MESMA forma que o
    backend Loki devolvia, porque o contrato REST não muda por troca de
    backend de observabilidade."""
    # `execution_id` vem do banco (é o PK uuid da execução), mas a NRQL é
    # string interpolada: recusa qualquer coisa que não seja uuid/hex-hífen,
    # mesmo padrão do wrapper e do `build_invocation`.
    if not all(c.isalnum() or c == "-" for c in execution_id):
        raise NewRelicLogsUnavailable(f"execution_id inválido para consulta: {execution_id!r}")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resposta = await client.post(
                nerdgraph_url,
                headers={"API-Key": api_key, "Content-Type": "application/json"},
                json={
                    "query": _NERDGRAPH_QUERY,
                    "variables": {
                        "accountId": int(account_id),
                        "nrql": _nrql(execution_id, inicio, fim, limite),
                    },
                },
            )
    except httpx.HTTPError as exc:
        raise NewRelicLogsUnavailable(f"NerdGraph inalcançável: {exc}") from exc

    if resposta.status_code != 200:
        raise NewRelicLogsUnavailable(f"NerdGraph respondeu {resposta.status_code}")

    corpo = resposta.json()
    if corpo.get("errors"):
        raise NewRelicLogsUnavailable(f"NerdGraph recusou a consulta: {corpo['errors']}")

    resultados = (
        corpo.get("data", {}).get("actor", {}).get("account", {})
        .get("nrql", {}).get("results") or []
    )
    linhas = [
        {
            # timestamp do New Relic é ms; a forma do endpoint é ns, como no Loki.
            "timestamp_ns": str(int(r.get("timestamp", 0)) * 1_000_000),
            "line": r.get("message") or "",
        }
        for r in resultados
    ]
    linhas.sort(key=lambda linha: linha["timestamp_ns"])
    return linhas
