# ADR-003 — Banco de Auditoria e Catálogo

- **Status**: aberto

## Decisão default

Banco relacional gerenciado (**RDS PostgreSQL**) como padrão.

## Pendências

Definir retenção e controles de imutabilidade (particionamento + write locks em registros históricos) conforme mandatos de compliance — a trilha cobre operações sobre arquivos de clearing e dados de portador de cartão.
