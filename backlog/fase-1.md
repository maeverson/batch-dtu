# Fase 1 — Back Office + Execução Controlada (RBAC + Auditoria)

**Objetivo**: remover a operação humana direta via SSH **sem alterar o plano de execução** — cron continua agendando, `main.sh` continua executando; toda ação manual passa por interface autenticada, autorizada e auditada.

## Escopo funcional

| Capacidade | Detalhe |
|---|---|
| Catálogo | Import do inventário (509 entradas) como seed; busca/filtro por domínio, cliente, ambiente, status |
| Execução manual | Dispara `main.sh --process-file X --manual-steps Y --dates-pattern-files Z`, com pré-validação (`--validate-file`) e confirmação explícita de data-alvo |
| Reprocesso multi-data | Formaliza o laço sequencial do SOP Zinli: API serializa datas e impede concorrência por processo |
| Monitoramento | Status de execução + streaming de logs (consultas Loki por `execution_id`/arquivo) |
| Auditoria | Registros imutáveis: ator, timestamp, processo, steps, data-alvo, origem (UI/API), resultado |
| Habilitar/Desabilitar | Registro de intenção + geração de instrução; atualização do crontab segue manual nesta fase, com reconciliação automatizada (diff crontab × catálogo) |

## RBAC (Entra ID) — roles mínimas

`batch.viewer` · `batch.operator` (UAT/TEST) · `batch.operator-prod` (PROD, confirmação reforçada p/ `upload_remote`) · `batch.admin` — ver `docs/seguranca.md`.

## Critérios de aceite da fase

- [ ] Zero execuções manuais via SSH fora do fluxo break-glass (medido por auditoria do sshd × logs de auditoria da plataforma).
- [ ] 100% das execuções manuais com registro de auditoria.
- [ ] Reprocessamento Zinli/MFTech executado fim-a-fim via Back Office como marco de validação.
