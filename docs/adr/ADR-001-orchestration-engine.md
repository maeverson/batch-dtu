# ADR-001 — Engine de Orquestração

- **Status**: aberto (decisão-alvo: kick-off da Fase 2)

## Contexto

O orchestrator gerencia ciclo de vida de execução: fila, concorrência por recurso (ex.: serializar execuções contra o mesmo SFTP de destino), retries com backoff, dependências entre jobs (DAG) e timeouts.

## Opções

**(a) EventBridge Scheduler + Step Functions + ECS Fargate** — totalmente gerenciado, alinhado à stack AWS atual (Amazon Linux, S3), pay-per-use, capacidade de DAG adequada às cadeias do parque atual.

**(b) Temporal** — superior em estado long-running e retries, ideal para workflows complexos; exige hosting/licença comercial.

**(c) Airflow (MWAA)** — ecossistema maduro, mas fortemente orientado a DAG-as-code em Python, o que conflita com o princípio "contrato JSON como interface estável".

## Recomendação preliminar

**(a)**, revisitando Temporal caso a Fase 3 introduza workflows longos com humano no loop.
