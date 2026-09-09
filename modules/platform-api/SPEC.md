# SPEC — Platform API

## Objetivo

API REST que abstrai o mecanismo de execução e concentra autenticação, autorização, validação e auditoria de toda operação sobre o parque de jobs.

## Requisitos funcionais

1. **Catálogo**: CRUD de leitura + habilitar/desabilitar com `reason`; busca/filtro por domínio, cliente, ambiente, status; endpoint de validação de contrato (`--validate-file`).
2. **Execução manual**: recebe `job_id`, `steps?`, `dates_pattern`, `no_mail?`, `justification`; pré-valida contrato; exige confirmação explícita de data-alvo (dupla confirmação quando houver step `upload_remote` em PROD).
3. **Reprocesso multi-data**: serializa datas sequencialmente e **bloqueia concorrência por processo** (lock por `job_id`). Nunca paralelizar — o SOP legado documenta timeout e contenção.
4. **Monitoramento**: status de execuções + proxy de logs via consulta Loki por `execution_id`.
5. **Auditoria**: leitura de `audit_event` com filtros; escrita automática em toda ação.
6. **Fase 1 — enable/disable**: registra intenção e gera instrução de mudança; reconciliação automatizada crontab × catálogo (job periódico que reporta diffs).

## Requisitos não funcionais

- OIDC/Entra ID; RBAC por role + escopo (domínio, ambiente). `batch.operator` não opera PROD.
- Idempotência: requisições de execução com chave de idempotência.
- `execution_id` injetado no nome do arquivo de log (Fase 1) e propagado em header/contexto (Fase 2).

## Backend Fase 1 (SSH parametrizado)

- Conta `backoffice_svc` (≠ `batch_user`); `authorized_keys` com `command=` → wrapper server-side que valida e só aceita invocações legítimas de `main.sh`.
- Mapeamento: `job_id` → `--process-file`; `steps` → `--manual-steps`; `dates_pattern` → `--dates-pattern-files`; `no_mail` → `--no-mail`.
- Execução assíncrona com acompanhamento (stream de status/logs).

## Backend Fase 2 (orchestrator)

- Mesma interface `ExecutionBackend`; a implementação enfileira no orchestrator e acompanha o ciclo de vida.

## Critérios de aceite

- [ ] Contrato REST estável validado por testes de contrato (a UI não quebra ao trocar backend).
- [ ] Nenhum caminho de código concatena entrada do usuário em comando shell (teste estático + revisão).
- [ ] 100% das ações de escrita com `audit_event` correspondente.
- [ ] Reprocesso Zinli/MFTech fim-a-fim via API (marco de validação da Fase 1).
