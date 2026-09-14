# SPEC — Observabilidade

## Entregas por fase

- **Fase 1**: API injeta `execution_id` no nome do arquivo de log; auditoria linka direto ao stream Loki correspondente; painel de execuções manuais.
- **Fase 2**: logs estruturados JSON e métricas emitidas nativamente pelo Executor v3 (fim dos problemas de `chmod` em árvores de log do runbook).
- **Fase 3**: SLOs por job derivados do catálogo → alertas de **atraso e ausência** ("Job X deveria ter concluído às 09:00 e não concluiu"); dashboard diário de saúde (execuções realizadas × esperadas).

## Métricas

- Por execução: duração, resultado, bytes/arquivos transferidos, retries.
- Por plataforma: profundidade de fila, concorrência, saturação por destino.

## Detecção de jobs fantasma

Monitorar execuções/logs sem `execution_id` conhecido (agendas fora do catálogo: crontab de root, outros hosts, triggers externos) antes do code freeze da Fase 2.

## Critérios de aceite

- [x] Toda execução via plataforma localizável no Loki por `execution_id` — `docker/promtail/config.yaml`
  embarca `logs/backoffice/<domínio>/<processo>.<execution_id>.log` (nome gravado pelo
  `batch-wrapper.sh`) para o Loki com `execution_id` como label de stream; `GET /executions/{id}/logs`
  já consulta por esse seletor. Verificação ponta a ponta pendente do container `docker/legacy` (não
  builda neste ambiente — ver ROADMAP Etapa 1.3).
- [x] Painel de execuções manuais — `docker/grafana/dashboards/execucoes-manuais.json`, verificado
  contra o Postgres real deste ambiente (ver ROADMAP Etapa 1.3).
- [ ] Alerta de ausência disparando em simulação de execução perdida (Fase 3).
- [ ] Dashboard realizado × esperado alimentado pelo catálogo (Fase 3).
