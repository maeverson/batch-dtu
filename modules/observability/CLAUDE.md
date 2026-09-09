# Módulo: Observabilidade

Contexto ao trabalhar aqui:
- A base Grafana Cloud (Promtail → Loki; Alloy → Mimir) **permanece** — não substituir, evoluir a fonte dos dados.
- O gap a fechar: execução **ausente** é invisível hoje. O objetivo qualitativo da Fase 3 é alertar sobre o que não rodou.
- `execution_id` é a chave de correlação em tudo (API → Orchestrator → Executor → Loki → auditoria).
