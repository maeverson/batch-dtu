# Módulo: Orchestrator

Contexto ao trabalhar aqui:
- Gerencia o ciclo de vida de execução: fila, concorrência, retries, DAGs, timeouts. Decisão de engine em aberto (ADR-001; recomendação preliminar: Step Functions + ECS Fargate).
- **Regra de ouro herdada do SOP Zinli**: nunca paralelizar execuções contra o mesmo SFTP de destino — isso é política de plataforma, não convenção operacional.
- Todo evento de ciclo de vida grava em `execution`/`audit_event` e propaga `execution_id`.
