# Platform API — Contrato REST (rascunho)

Autenticação: **Entra ID (OIDC)**. Autorização: roles `batch.*` com escopo domínio/ambiente. **O contrato não muda entre fases** — apenas o backend (Fase 1: SSH parametrizado contra o legado; Fase 2: enfileiramento no orchestrator).

## Recursos

### Catálogo
```
GET    /jobs                      # busca/filtro: domain, client, environment, status
GET    /jobs/{id}
POST   /jobs/{id}/validate        # equivalente a --validate-file
PATCH  /jobs/{id}/status          # habilitar/desabilitar com reason (Fase 1: registra intenção,
                                  # gera instrução; crontab segue manual + reconciliação automatizada)
```

### Execuções
```
POST   /executions                # dispara execução manual
GET    /executions?job_id=&from=&to=&result=
GET    /executions/{id}
GET    /executions/{id}/logs      # proxy de consulta Loki por execution_id
```

Payload de `POST /executions`:
```json
{
  "job_id": "…",
  "steps": ["3", "4"],              // opcional — semântica --manual-steps
  "dates_pattern": ["2026-09-01"],  // obrigatória; múltiplas datas => serialização sequencial
  "confirm_target_dates": true,     // confirmação explícita de data-alvo (dupla p/ upload_remote)
  "no_mail": false,
  "justification": "…"
}
```

Regras:
- Pré-validação (`--validate-file`) sempre antes de executar.
- Múltiplas datas são **serializadas** e a API **impede concorrência por processo** (formalização do laço do SOP Zinli).
- Steps `upload_remote` em PROD: confirmação reforçada (Fase 1) → aprovação two-person (Fase 3, recurso `approvals`).

### Aprovações (Fase 3)
```
POST   /approvals                  # criada automaticamente para reprocessos sensíveis
GET    /approvals?status=pending
POST   /approvals/{id}/decision    # approve | reject + justificativa (aprovador ≠ solicitante)
```

### Auditoria
```
GET    /audit-events?actor=&action=&target=&from=&to=   # append-only, somente leitura
```

## Backend Fase 1 (transitório)

- Executa via SSH parametrizado contra o host legado, conta `backoffice_svc`, `authorized_keys` com `command=` apontando para wrapper que só aceita invocações válidas de `main.sh`.
- A API monta a linha de comando **apenas** a partir de campos tipados; o wrapper revalida server-side. Nunca interpolar entrada livre do usuário.
- Toda chamada gera `execution` + `audit_event` e injeta `execution_id` no nome do arquivo de log.
