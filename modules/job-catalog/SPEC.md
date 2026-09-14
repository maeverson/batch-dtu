# SPEC — Job Catalog

## Objetivo

Transformar o inventário do parque de documento em **tabela versionada e viva**.

## Requisitos

1. **Modelo**: `job` com contrato JSON (versionado, `schema_version`), domínio, cliente, país, ambiente, agenda + timezone, status (ativo/desabilitado + reason), owner, criticidade, SLA.
2. **Seed**: importador do inventário consolidado (coleta de 09/2026: 597 linhas de job, 393 ativas e 204 desabilitadas/on-demand, consolidadas em 527 jobs distintos), preservando comentários do crontab como `status_reason`.
3. **Validação de schema**: contrato validado na escrita; Fase 3 → validação contínua de todo o catálogo.
4. **Versionamento**: histórico de versões do contrato e dos metadados; diffs consultáveis.
5. **Reconciliação (Fase 1)**: job de diff crontab × catálogo com relatório de divergências; detecção de "jobs fantasma" (execuções sem `execution_id`/entrada conhecida).
6. **Fase 2**: catálogo passa a **gerar** as agendas do scheduler gerenciado; crontab vira artefato derivado.

## Critérios de aceite

- [ ] 597 linhas de job importadas (393 ativas, 204 desabilitadas) como 527 jobs distintos, com domínio/status/razão corretos (validação amostral por domínio).
- [ ] Toda alteração de catálogo com `audit_event`.
- [ ] Relatório de reconciliação sem divergências não explicadas antes do início da Fase 2.
