# ADR-001 — Engine de Orquestração

- **Status**: aceito
- **Data**: 2026-09-16
- **Decisores**: Maéverson Waitman

## Contexto

O orchestrator gerencia ciclo de vida de execução: fila, concorrência por recurso (ex.: serializar execuções contra o mesmo SFTP de destino), retries com backoff, dependências entre jobs (DAG) e timeouts.

## Opções consideradas

**(a) EventBridge Scheduler + Step Functions + ECS Fargate** — totalmente gerenciado, alinhado à stack AWS atual (Amazon Linux, S3), pay-per-use, capacidade de DAG adequada às cadeias do parque atual.

**(b) Temporal** — superior em estado long-running e retries, ideal para workflows complexos; exige hosting/licença comercial.

**(c) Airflow (MWAA)** — ecossistema maduro, mas fortemente orientado a DAG-as-code em Python, o que conflita com o princípio "contrato JSON como interface estável".

## Decisão

**(a) EventBridge Scheduler + Step Functions + ECS Fargate.**

Gerenciado, pay-per-use, alinhado à stack AWS já em uso, e suficiente para as cadeias de dependência do parque atual (`parte1` → `parte2`, serialização por destino). Revisitar Temporal se a Fase 3 introduzir workflows longos com humano no loop (`approval`, two-person rule) que Step Functions não modele bem.

## Consequências

- `orchestrator` (Etapa 2.3) é construído sobre Step Functions; segunda implementação de `ExecutionBackend` na Platform API enfileira execuções como Step Functions state machine executions, sem mudar o contrato REST.
- `executor-v3` (Etapa 2.2) empacota como container ECS Fargate — consistente com a decisão de ADR-002 (containerizar o shell legado primeiro).
- Serialização por destino/cliente (item da Etapa 2.3) mapeia para concorrência controlada por Step Functions + filas por recurso, não por lock de aplicação.
- Descartado Airflow: DAG-as-code em Python quebraria o invariante "contrato JSON é interface estável" — o parque continuaria descrito em contrato declarativo, não em DAGs Python versionadas separadamente.
