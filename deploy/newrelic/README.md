# New Relic — o que configurar

Duas pontas, uma chave de correlação (`execution_id`):

| Onde | O quê | Arquivo |
|---|---|---|
| Pod da Platform API | APM + evento `BatchExecution` | `newrelic.ini` (+ `NEW_RELIC_*` no env do serviço) |
| Host do `main.sh` | log de execução manual | `logging.d/batch-backoffice.yml` |

## 1. Regra de parsing (uma vez, na conta)

O `execution_id` está no **nome do arquivo**, não no corpo da linha. Sem esta
regra o log chega, mas `GET /executions/{id}/logs` devolve vazio — e execução
"sem log" é indistinguível, para quem opera, de execução que não logou.

Em **Logs → Parsing → Create parsing rule**:

- Filtro: `logtype = 'batch_backoffice'`
- Campo a parsear: `filePath`
- Grok:

```
%{GREEDYDATA}/logs/backoffice/%{DATA:domain}/%{DATA:process}\.%{DATA:execution_id}\.log
```

Valide com uma linha real antes de salvar: a regra só vale para o que chegar
**depois** dela.

## 2. Timestamp da linha

O `main.sh` e o wrapper emitem RFC3339 no início de cada linha. Configure o
parsing de timestamp para usá-lo — senão o New Relic carimba a hora de
**ingestão**, a linha cai fora da janela que a API consulta, e a execução
aparece sem log. Foi exatamente esse defeito que apareceu na verificação do
promtail em 16/09/2026 (`ROADMAP.md`, Etapa 1.3).

## 3. Consultas que a plataforma usa

A API consulta por NerdGraph (`src/platform_api/newrelic_logs.py`):

```sql
SELECT timestamp, message FROM Log WHERE execution_id = '<uuid>' SINCE ... UNTIL ...
```

Painel de execuções manuais (substitui o dashboard Grafana da Etapa 1.3):

```sql
SELECT count(*) FROM BatchExecution FACET status SINCE 1 day ago
SELECT percentage(count(*), WHERE status = 'succeeded') FROM BatchExecution SINCE 1 day ago
SELECT * FROM BatchExecution WHERE environment = 'PROD' SINCE 1 day ago LIMIT 100
```

## 4. Alerta que a Fase 1 precisa ter

Com `POST /executions` respondendo 200 antes do desfecho, **execução presa em
`running` deixou de ser visível para quem disparou**. Esse alerta é o que
substitui o feedback que a resposta síncrona dava:

```sql
SELECT count(*) FROM BatchExecution WHERE status = 'running' SINCE 30 minutes ago
```

Condição: `> 0` por 30 minutos (ajuste pela duração real do job mais longo).

## 5. Chaves

- `NEW_RELIC_LICENSE_KEY` — ingestão (agente APM e agente de log). 
- `NEW_RELIC_API_KEY` — **User Key**, só consulta, usada pela API para ler log.

São chaves diferentes de propósito: a de consulta não ingere, a de ingestão não
lê. Ambas do Secrets Manager, nunca em arquivo versionado.
