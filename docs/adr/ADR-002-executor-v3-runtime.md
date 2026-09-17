# ADR-002 — Runtime do Executor v3

- **Status**: aceito
- **Data**: 2026-09-16
- **Decisores**: Maéverson Waitman

## Contexto

Reimplementar as capacidades de `main.sh` em linguagem de serviço moderna (Go/Python/Java) vs. containerizar o framework shell existente como passo intermediário.

## Opções consideradas

**(a) Containerizar o shell legado** — empacota `main.sh` e o framework atual (`batch-commons-framework`) num container, mantendo a implementação shell, mas rodando em ECS Fargate como o resto da Fase 2. Acelera o descomissionamento dos servidores físicos (antecipa parte da Fase 2), mas adia a resolução da contenção de heap fixo de 2 GB dos JARs — essa dívida migra junto para o container.

**(b) Reimplementar em linguagem de serviço (Go/Python/Java)** — resolve heap/performance desde já, mas atrasa o cutover porque exige paridade funcional completa (verificada pela suíte de testes de contrato) antes de qualquer job migrar.

## Decisão

**(a) Containerizar o shell legado primeiro.**

Prioriza desligar/reduzir `Batch-Prod-srv-sftp-2-120` o quanto antes (marco da Fase 2), aceitando que a contenção de heap dos JARs continua sem solução neste passo. A reimplementação em linguagem de serviço fica como evolução posterior do runtime do executor, sem bloquear as waves de migração.

## Requisito independente da escolha

Paridade funcional total com `main.sh` (funções de step, placeholders de data, `stop_on_failed`, `send_infra_mail`, seleção manual de steps), verificada pela suíte de testes de contrato — condição de partida da Etapa 2.2, antes de qualquer conector novo.

## Consequências

- `executor-v3` (Etapa 2.2) começa pela suíte de testes de contrato contra `main.sh` como baseline, depois empacota o próprio shell (não uma reescrita) em container ECS Fargate (ADR-001).
- A contenção de heap fixo de 2 GB por execução **não é resolvida** nesta etapa — permanece como item explícito de risco/débito técnico a revisitar (candidato natural: Etapa 3.5, "right-sizing por job + fim do heap fixo").
- O cutover por wave (Etapa 2.5) valida shadow execution do container contra o host físico antes de migrar cada domínio — a superfície de risco é operacional (mesma lógica, ambiente novo), não funcional.
- Se a paridade funcional (placeholders, `stop_on_failed`, `custom_scripts/`) não fechar na suíte de contrato, o container não substitui main.sh — reabre a decisão antes do cutover da wave correspondente.
