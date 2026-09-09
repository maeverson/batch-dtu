# ADR-002 — Runtime do Executor v3

- **Status**: aberto

## Contexto

Reimplementar as capacidades de `main.sh` em linguagem de serviço moderna (Go/Python/Java) vs. containerizar o framework shell existente como passo intermediário.

## Trade-off

Containerizar o shell **acelera o descomissionamento dos servidores** (antecipa parte da Fase 2), mas **adia a resolução da contenção de heap dos JARs** (2 GB fixos por execução).

## Requisito independente da escolha

Paridade funcional total com `main.sh` (funções de step, placeholders de data, `stop_on_failed`, `send_infra_mail`, seleção manual de steps), verificada pela suíte de testes de contrato.
