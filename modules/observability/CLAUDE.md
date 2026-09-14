# Módulo: Observabilidade

Contexto ao trabalhar aqui:
- A base Grafana Cloud (Promtail → Loki; Alloy → Mimir) **permanece** — não substituir, evoluir a fonte dos dados.
- O gap a fechar: execução **ausente** é invisível hoje. O objetivo qualitativo da Fase 3 é alertar sobre o que não rodou.
- `execution_id` é a chave de correlação em tudo (API → Orchestrator → Executor → Loki → auditoria).
- Entregas da Etapa 1.3 (dev local, espelho do Grafana Cloud): agente em `docker/promtail/config.yaml`
  (serviço `promtail`, profile `legacy`/`all`) embarca `logs/backoffice/**/*.log` do host legado simulado
  para o Loki, com `execution_id` extraído do **nome do arquivo** (convenção do `batch-wrapper.sh`) como
  label de stream — é esse label que `GET /executions/{id}/logs` consulta. Painel de execuções manuais em
  `docker/grafana/dashboards/execucoes-manuais.json`, direto em `catalog.execution`/`catalog.job` via o
  datasource Postgres novo em `docker/grafana/provisioning/datasources/datasources.yaml`.
