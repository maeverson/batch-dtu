# Observabilidade e SLOs

Base instalada permanece: Promtail → Loki; Alloy → Mimir; dashboards Back Office e Linux Server (Grafana Cloud).

## Evolução por fase

- **Fase 1**: API injeta `execution_id` no nome dos arquivos de log → registro de auditoria linka direto ao stream de log correspondente.
- **Fase 2**: Executor v3 emite logs estruturados (JSON) e métricas nativamente — elimina problemas de permissão (`chmod`) em árvores de log apontados no runbook.
- **Fase 3**: SLOs por job derivados do catálogo alimentam alertas de **atraso e ausência** ("Job X deveria ter concluído às 09:00 e não concluiu") + dashboard diário de saúde (execuções realizadas vs. esperadas).

## Correlação

`execution_id` correlaciona API → Orchestrator → Executor → Loki. Todo log e métrica de execução carrega esse identificador.

## Métricas core

- **Por execução**: duração, resultado, bytes/arquivos transferidos, retries.
- **Por plataforma**: profundidade de fila, concorrência, saturação por destino.

## Mudança qualitativa

O gap principal do estado atual é que *execução ausente é invisível*. A plataforma deve alertar sobre o que **não** rodou, não apenas sobre erros em logs.
