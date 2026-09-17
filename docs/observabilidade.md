# Observabilidade e SLOs

**Backend da plataforma: New Relic** (decisão do cliente, 17/09/2026) — APM no processo da
Platform API, log do host embarcado pelo agente de infraestrutura, e consulta por NerdGraph.
Configuração em `deploy/newrelic/`. O Grafana Cloud (Promtail → Loki, Alloy → Mimir) continua
existindo para o que já monitorava; o `docker-compose.yaml` deste repositório segue com
Loki/Grafana, que é o que roda offline no desenvolvimento (`LOG_BACKEND=loki|newrelic`).

## Evolução por fase

- **Fase 1**: API injeta `execution_id` no nome dos arquivos de log → registro de auditoria linka
  direto ao log correspondente. Como `POST /executions` responde antes do desfecho, o
  acompanhamento de uma execução em andamento é no New Relic — e **alerta de execução presa em
  `running` passa a ser obrigatório já nesta fase**, não na Fase 3: ele é o que substitui o exit
  code que a resposta síncrona devolvia.
- **Fase 2**: Executor v3 emite logs estruturados (JSON) e métricas nativamente — elimina problemas de permissão (`chmod`) em árvores de log apontados no runbook.
- **Fase 3**: SLOs por job derivados do catálogo alimentam alertas de **atraso e ausência** ("Job X deveria ter concluído às 09:00 e não concluiu") + dashboard diário de saúde (execuções realizadas vs. esperadas).

## Correlação

`execution_id` correlaciona API → Orchestrator → Executor → log. Todo log, evento e métrica de
execução carrega esse identificador. No New Relic ele chega ao log pelo **nome do arquivo**
(regra de parsing sobre `filePath`) e ao APM como atributo da transação — ver
`deploy/newrelic/README.md`.

## Métricas core

- **Por execução**: duração, resultado, bytes/arquivos transferidos, retries.
- **Por plataforma**: profundidade de fila, concorrência, saturação por destino.

## Mudança qualitativa

O gap principal do estado atual é que *execução ausente é invisível*. A plataforma deve alertar sobre o que **não** rodou, não apenas sobre erros em logs.
