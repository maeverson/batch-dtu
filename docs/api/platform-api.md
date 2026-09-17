# Platform API — Contrato REST (rascunho)

Autenticação e autorização: **Entra ID (OIDC)** — as app roles `batch.*` do token são a
autorização inteira. **O contrato não muda entre fases** — apenas o backend (Fase 1: SSH
parametrizado contra o legado; Fase 2: enfileiramento no orchestrator).

**Uma instância serve um ambiente e um host.** `PLATFORM_ENVIRONMENT`/`PLATFORM_HOST` definem o
recorte: UAT e PROD são deploys distintos, e um job fora do recorte não é listado nem operado —
nem por `batch.admin`. `GET /health` e `GET /me` dizem qual recorte respondeu.

## Recursos

### Catálogo
```
GET    /jobs                      # busca/filtro: domain, client, environment, status
GET    /jobs/{id}
GET    /jobs/{id}/schedules       # agendas do job (schedule_expr, timezone, raw_line)
GET    /jobs/{id}/contract        # contrato JSON da versão corrente
GET    /jobs/{id}/reconciliation  # divergências ABERTAS na reconciliação mais recente do host do job
POST   /jobs/{id}/validate        # equivalente a --validate-file
PATCH  /jobs/{id}/status          # habilitar/desabilitar com reason
```

`GET /jobs/{id}/reconciliation` responde `{"host", "state": "ok"|"divergente"|"nunca_rodou",
"run_id", "run_finished_at", "open_findings": [...]}` — alimenta o estado de reconciliação que o
Back Office mostra ao lado de habilitar/desabilitar (Etapa 1.4). Todos os três GETs exigem só
visibilidade do job (mesma regra de `GET /jobs/{id}`), não a role de operar.

`PATCH /jobs/{id}/status` grava o estado desejado no catálogo **e** abre um `crontab_change_request`. A resposta traz a **linha-alvo** a aplicar (não um diff do arquivo, que pode não aplicar mais quando o operador executar), já com o marcador `#BO:{job_id}:{change_id}`:

```json
{
  "change_id": "…",
  "state": "pending",
  "expires_at": "2026-09-13T19:00:00Z",
  "instruction": {
    "action": "comment",
    "source": "batch_user@/var/spool/cron/batch_user",
    "lineno": 587,
    "target_line": "#BO:{job_id}:{change_id} cliente suspendeu o contrato\n#00 02 * * * /fw/schedulers/otros/prd_aaa_col_otr.sh >> /fw/logs/x.log"
  }
}
```

### Mudanças de agendamento
```
GET    /change-requests?state=pending&host=      # worklist do Back Office
POST   /change-requests/{id}/cancel              # desistência, com reason
```

Ciclo: `pending → applied → verified` (+ `cancelled`, `expired`). A verificação é feita pela reconciliação por **detecção do estado observado**, não por declaração do operador. Divergência sem request aberta é `drift-nao-gerenciado` — o detector de edição de crontab fora do fluxo. Pendência além do SLA alarma, porque desabilitar no catálogo **não para o cron**.

### Execuções
```
POST   /executions                # despacha execução manual — 200 imediato
GET    /executions?job_id=&from=&to=&result=
GET    /executions/{id}
GET    /executions/{id}/logs      # consulta por execution_id (New Relic; Loki no dev local)
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

**`POST /executions` responde `200` com `status: "running"` assim que despacha** — a resposta é o
comprovante do despacho, não o desfecho. O `main.sh` segue rodando no host; o desfecho cai em
`GET /executions/{id}` e o acompanhamento em tempo real é no New Relic, pelo `execution_id`
(evento `BatchExecution` + log correlacionado).

Regras:
- Pré-validação (`--validate-file`) sempre antes de executar — **síncrona**, e é ela que dá
  sentido ao 200: nada é despachado sem o `main.sh` real ter aprovado o contrato.
- Duas travas de concorrência: lock advisory na janela de registro e **409 quando o job já tem
  execução `running`** (o lock morre no commit; o `main.sh` não).
- Múltiplas datas são **serializadas** e a API **impede concorrência por processo** (formalização do laço do SOP Zinli).
- Steps `upload_remote` em PROD: confirmação reforçada (Fase 1) → aprovação two-person (Fase 3, recurso `approvals`).

### Aprovações (Fase 3)
```
POST   /approvals                  # criada automaticamente para reprocessos sensíveis
GET    /approvals?status=pending
POST   /approvals/{id}/decision    # approve | reject + justificativa (aprovador ≠ solicitante)
```

### Administração (`batch.admin`)
```
GET    /admin/jobs?q=&domain=&status=   # todos os jobs do recorte da instância
POST   /admin/jobs                      # cria — host/environment vêm da instância
PATCH  /admin/jobs/{id}                 # atualiza metadados (reason obrigatório)
DELETE /admin/jobs/{id}                 # DESATIVA no catálogo; não apaga linha nenhuma
PUT    /admin/jobs/{id}/contract        # nova versão do contrato, validada pelo schema
```

Toda escrita exige `reason` e gera `audit_event` + `job_revision` na mesma transação. `DELETE` não
remove: job tem execução, auditoria e histórico apontando para ele. E desativar no catálogo **não
para o cron** — isso é `PATCH /jobs/{id}/status`.

### Auditoria
```
GET    /audit-events?actor=&action=&target=&from=&to=   # append-only, somente leitura
```

### Identidade
```
GET    /me   # subject, display_name, roles do token, environment/host da instância, is_admin
```

Usado pelo Back Office para renderização condicionada a role sem duplicar a regra de autorização
— o servidor continua sendo quem de fato autoriza cada ação; isto só evita oferecer na UI uma ação
que o 403 recusaria.

## Backend Fase 1 (transitório)

- Executa via SSH parametrizado contra o host legado, conta `backoffice_svc`, `authorized_keys` com `command=` apontando para wrapper que só aceita invocações válidas de `main.sh`.
- A API monta a linha de comando **apenas** a partir de campos tipados; o wrapper revalida server-side. Nunca interpolar entrada livre do usuário.
- Toda chamada gera `execution` + `audit_event` e injeta `execution_id` no nome do arquivo de log.
- A confiança no canal é chave pública + `known_hosts` (`SSH_BACKEND_KNOWN_HOSTS`, obrigatório: sem ele a API não sobe).

**Como o `execution_id` chega ao log, sem tocar no legado.** O `main.sh` real **não aceita** identificador de execução — flag desconhecida é fatal nele (`Opcion desconocida` → `main_help` → `exit`), então repassá-la faria *toda* execução via API falhar antes do primeiro step. Quem controla o redirecionamento é o wrapper do `command=`: ele recebe `--execution-id` da API, **não o repassa**, e usa o valor para nomear o próprio arquivo (`logs/backoffice/<domínio>/<processo>.<execution_id>.log`), com `tee` para preservar o stream que a API acompanha e `PIPESTATUS` para não mascarar o código de saída do job. A linha `ALLOW` da auditoria do wrapper liga `execution_id` ↔ arquivo.

O `main.sh` continua nomeando o log interno dele por data; os dois convivem e `execution.log_link` guarda a correlação.
