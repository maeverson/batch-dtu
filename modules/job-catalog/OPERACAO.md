# Job Catalog — Guia de operação (CLI)

Referência de uso do pacote `catalog` (`src/catalog/`). O `CLAUDE.md` deste módulo dá o
contexto; o `SPEC.md` dá os requisitos e critérios de aceite. Este documento é o que faltava
entre os dois: **como um operador roda cada comando**, na ordem em que eles se encaixam.

Instalação: `pip install -e ".[dev,postgres]"` a partir da raiz do repo (ver `pyproject.toml`).
Banco: `docker compose up -d postgres` e `alembic upgrade head` com `DATABASE_URL_MIGRATIONS`
apontando para a role `batch_migrator` (ver `docker/README.md`). Os comandos que tocam banco
usam `DATABASE_URL` (role `batch_app` por padrão; `--role migrations|test` troca a role/URL).

## Fluxo, do pacote de coleta ao catálogo consultável

```
collect-seed.sh (no host)  →  catalog import  →  catalog load  →  catalog reconcile
                                                                        │
                                                          catalog triage / catalog explain
                                                          catalog sample
                                                          catalog history
```

### 1. `catalog import <pacote>` — monta o catálogo em memória, sem tocar banco

```
catalog import seed/raw/batch-seed-<host>-<stamp> --out seed/raw/reports
```

Lê o pacote extraído pelo coletor (`seed/collect/`), aplica o vocabulário de curadoria
(`seed/mappings/vocabulary.yaml`, ou outro via `--vocabulary`) e escreve em `--out`:
`import-<host>.md` (relatório), `jobs-<host>.csv`, `findings-<host>.csv`,
`clients.todo-<host>.yaml` (códigos de cliente sem nome mapeado — **não adivinhar**: cliente
errado num `upload_remote` é arquivo no destino errado) e `clients.derived-<host>.yaml`.

Saída não-zero se houver achado de severidade **erro** — trave de CI antes de carregar.
`seed/raw/` é gitignored: os relatórios contêm nome de cliente, IP e caminho interno.

### 2. `catalog load <pacote>` — persiste, idempotente

```
catalog load seed/raw/batch-seed-<host>-<stamp> --actor voce@contabilizei.com.br
```

Repete o `import` e persiste em uma transação: `job`, `job_schedule`,
`job_contract_version` (por hash — rodar duas vezes não duplica versão), `job_revision`
(diff de metadados), `connection_alias`, `crontab_snapshot`/`crontab_entry` (linha a linha,
inclusive comentário e branco), `reconciliation_run`/`reconciliation_finding`, e
`audit_event` na mesma transação da mudança.

- `--dry-run` roda tudo e reverte — útil para ver o relatório sem persistir.
- `--role migrations|test` troca a URL de conexão (ver `catalog/db/session.py`).
- Sob `Policy.STRICT` (não é o default do `load`; ver `contract_schema.py`) um contrato
  reprovado no schema aborta a transação inteira via `ContractInvalid`. O `load` usa
  `Policy.LEGACY`: registra o veredito em `job_contract_version.validation_status` e não
  bloqueia — o catálogo mostra o parque como ele é, inclusive quebrado.

### 3. `catalog reconcile <pacote>` — diff crontab × catálogo, sem escrever job/agenda/contrato

```
catalog reconcile seed/raw/batch-seed-<host>-<stamp> --actor voce@contabilizei.com.br \
  --out seed/raw/reports/reconcile-<host>.csv
```

Compara uma coleta **fresca** contra o que já está no banco. Cinco classes de divergência:
`job-fantasma` (agenda sem catálogo), `job-sem-entrada-no-crontab`, `agenda-divergente`,
`contrato-divergente` (hash em disco ≠ versão corrente), `metadado-divergente`. Os achados do
`import` (alias não declarado, contrato ausente, cliente-outlier…) entram no mesmo run.

Fecha o loop de `crontab_change_request` (ver "Ciclo de mudança" no `CLAUDE.md` do módulo
`platform-api`): divergência de status **explicada por um pedido aberto** vira
`mudanca-em-andamento` (info) ou `mudanca-verificada`; **sem pedido**, vira
`drift-nao-gerenciado` (erro); pedido vencido vira `mudanca-pendente-vencida` (erro, porque
desabilitar no catálogo não para o cron).

`--fail-on-open/--no-fail-on-open` (default: falha) — sai 1 se houver divergência aberta de
severidade erro. `--dry-run` roda e reverte.

### 4. `catalog triage` — os achados abertos, agrupados por quem decide

```
catalog triage --out seed/raw/reports/triagem-owners.md
catalog triage --host srv-sftp-2                 # só um host
catalog triage --todos                           # inclui os já explicados
```

Lê a **última** reconciliação de cada host (ou do host passado em `--host`) e agrupa os
achados por cliente/domínio — resolvendo até o job mesmo quando o achado só cita um alias.
Grupo com job **ativo** que faz `upload_remote` vem primeiro (risco corrente, não dívida de
catálogo). Cada linha traz o `fingerprint` pronto para o próximo comando.

Sem `--host`, roda em todos os hosts do catálogo (`catalog/db/queries.py:hosts_no_catalogo`).

### 5. `catalog explain <fingerprint>` — fecha uma divergência com justificativa auditada

```
catalog explain <fingerprint> --reason "alias migra na 2.1, vault ja resolve" \
  --actor voce@contabilizei.com.br
```

Marca **toda ocorrência** daquele fingerprint como `explained`. A explicação sobrevive às
próximas reconciliações (a chave é o fingerprint, não o id do run) e gera `audit_event`
`reconciliation.finding.explain`. É o mecanismo que fecha o critério "sem divergência não
explicada" — sem ele, cada reconciliação reabriria a mesma conversa.

### 6. `catalog validate <arquivo|diretório>` — o schema fora do fluxo de carga

```
catalog validate seed/raw/batch-seed-<host>-<stamp>/framework/processes --strict
```

Roda `contract_schema.py` isoladamente — útil para validar um contrato antes de ele entrar
no pacote de coleta, ou para auditar o parque inteiro sem tocar banco. `--strict` faz o
comando sair 1 se houver contrato inválido (sem `--strict`, só reporta).

### 7. `catalog sample <host>` — checklist para a validação amostral por domínio

```
catalog sample srv-sftp-2 --por-dominio 3 --seed 20260911 --out seed/raw/reports/amostra-srv-sftp-2.md
```

Amostra **determinística**: mesma semente, mesma amostra — condição para a conferência ser
reproduzível e auditável (`--seed` default `20260911`; mudar a semente muda a amostra, então
fixe e registre a semente usada numa conferência). Cada item traz domínio/status/razão do
catálogo, as agendas e o que o **contrato** declara (client/country/environment/name_process)
lado a lado, para conferir contra o crontab real.

Esta é a peça que faltava para o critério de aceite "validação amostral por domínio" do
`SPEC.md` — a ferramenta gera o checklist; a conferência em si é humana.

### 8. `catalog history <processo>` — versionamento com diffs consultáveis

```
catalog history prd_stb_col_rpt --host srv-sftp-2   # --host obrigatório se existir nos dois
```

Junta `job_contract_version` (versão do contrato, hash, contagem de steps, veredito de
schema) e `job_revision` (metadados: domínio, cliente, status, flags…) em uma linha do
tempo, com o valor **antes → depois** de cada campo alterado. Sem `--host`, e o processo
existir em mais de um host (PROD e UAT são clones), o comando lista os hosts e pede para
escolher.

### 9. `catalog check-names <pacote>` — depuração do parser de nomes

```
catalog check-names seed/raw/batch-seed-<host>-<stamp>
```

Mostra a decomposição de cada nome de contrato (ambiente, cliente, país, domínio, sufixo,
flags) sem tocar banco. Útil ao curar `seed/mappings/vocabulary.yaml` — compare a coluna
`cliente` contra o que o `import` reportou em `clients.todo-<host>.yaml`.

## Onde cada critério de aceite é fechado

| Critério (`SPEC.md`) | Comando |
|---|---|
| Importação com domínio/status/razão | `catalog import` + `catalog load` |
| Validação amostral por domínio | `catalog sample` (ferramenta) — conferência é manual |
| Validação de schema na escrita | `catalog validate`; veredito em `job_contract_version` via `load` |
| Versionamento com diffs consultáveis | `catalog history` |
| `audit_event` em toda alteração | automático em `load`/`reconcile`/`explain` |
| Reconciliação sem divergência não explicada | `catalog reconcile` + `catalog triage` + `catalog explain` |
