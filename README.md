# Batch DTU — Documentação para desenvolvimento com Claude Code

Pacote de documentação derivado da *Architecture and Solution Specification — Scheduled Jobs Platform (Batch DTU)* (set/2026), estruturado para servir de contexto ao Claude Code no desenvolvimento de cada módulo.

## Como usar

1. Copie o conteúdo deste pacote para a raiz do repositório do projeto (ou de um monorepo).
2. O `CLAUDE.md` da raiz é carregado automaticamente pelo Claude Code e contém os invariantes do projeto.
3. Cada módulo em `modules/<nome>/` tem seu próprio `CLAUDE.md` (carregado quando você trabalha dentro do diretório) e um `SPEC.md` com requisitos, interfaces e critérios de aceite.
4. Ao iniciar o desenvolvimento de um módulo, peça ao Claude Code: *"leia modules/<nome>/SPEC.md e docs/contrato-json.md e proponha o plano de implementação"*.

## Ordem sugerida de desenvolvimento (Fase 1)

1. `job-catalog` — modelo de dados + seed do inventário (509 entradas)
2. `platform-api` — API REST com Entra ID, tradução para SSH parametrizado
3. `back-office` — frontend React
4. `observability` — injeção de `execution_id` nos logs, correlação com auditoria

Fase 2 adiciona: `executor-v3`, `scheduler`, `orchestrator`, `secrets-migration`.

## Fonte

Especificação de arquitetura Batch DTU/Batch V2 — Fases 1 a 3, inventário consolidado (ago/2026), runbook de observabilidade, SOP de reprocessamento Zinli/MFTech, backlog de fases.
