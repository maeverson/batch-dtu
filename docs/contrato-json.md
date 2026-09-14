# Contrato JSON — Interface Estável

**Este é o ativo mais valioso do sistema atual.** Todos os módulos que interpretam ou geram contratos devem tratá-lo como interface pública versionada.

## Regras

- Extensões são **sempre aditivas** e controladas por `schema_version`.
- Contratos **nunca** contêm senhas, IPs ou segredos — apenas *aliases* de conexão (resolvidos via `connections.json` no legado; via vault a partir da Fase 2).
- O executor (legado `main.sh` ou Executor v3) interpreta o mesmo contrato; a paridade é verificada por testes de contrato.

## Schema e validação

O schema é código: `src/catalog/contract_schema.py`, derivado dos 575 contratos reais da coleta de 09/2026 e verificado contra eles em `tests/test_contract_validation.py`. Um schema que reprovasse contrato que roda em produção hoje seria um schema errado — é a leitura operacional do invariante 1.

Campos do topo: `name_process`, `client`, `country`, `environment`, `description`, `send_infra_mail`, `steps` (obrigatórios) e `additional_info`. `schema_version` é **opcional**: nenhum contrato do parque o traz, então ausência é legado, nunca erro.

O que é **erro** é o que o executor não consegue executar — função fora das 9 conhecidas, step sem o campo que a função exige, tipo errado, item de `files` sem `file_name`. O que é **aviso** é campo desconhecido (extensão aditiva não pode ser recusada antes de existir) e numeração de step fora de `1..n`.

Duas políticas de escrita:

| Política | Onde | Comportamento |
|---|---|---|
| `LEGACY` | import/seed do parque | Valida e registra o veredito; nunca bloqueia. O catálogo mostra o parque como ele é, inclusive quebrado. |
| `STRICT` | escrita nova (Platform API) | Erro levanta `ContractInvalid` e a escrita não acontece. |

O veredito fica em `job_contract_version.validation_status` / `validation`, junto da versão e não do job: a versão é imutável, então o veredito é o que valia no momento da escrita. `catalog validate <arquivo|diretório>` roda o mesmo validador em lote.

## Semântica preservada obrigatoriamente

| Recurso | Semântica |
|---|---|
| Steps sequenciais | Executados em ordem; `stop_on_failed` por step decide se falha interrompe o job |
| `download` / `upload` | Transferência SFTP, S3 ou Azure Blob |
| `execute_command` | Comando local ou SSH remoto (a partir da Fase 2: restrito a hosts/scripts registrados no catálogo) |
| `download_remote` / `upload_remote` | Transferência via jump server; `upload_remote` = envio a cliente (step sensível). **A conexão é sempre o jump SFTP** (`$SFTP_SERVER_CUSTOMER_UPLOAD` → alias `sftp_customer_upload`); o `server_remote` do contrato é o **destino downstream**, passado como parâmetro para `send_remote_command.sh` no jump host — não é alias do `connections.json` local |
| `encrypt` / `decrypt` | GPG; chaves de cliente com validade monitorada (expiração é modo de falha conhecido) |
| `copy_local` | Cópia local de arquivos |
| `send_mail` | Notificação por e-mail (`send_infra_mail` incluída) |
| Placeholders de data | `@@@YYYY@@@`, `@@@JULIANO@@@`, `@@@YYYYMMDD@@@` (e demais do framework), resolvidos contra a data-alvo da execução |

### Alias de conexão × destino remoto

Distinção verificada no `main.sh` em 09/2026, e que o importador respeita:

| Campo do step | O que é | Onde resolve |
|---|---|---|
| `server` | **alias de conexão** | `connections.json` do host (Fase 2: vault) |
| `server_remote` | **destino downstream** do cliente | script no jump SFTP, fora deste host |

Confundir os dois tem consequência prática em dois lugares: gera falso
`alias-nao-declarado` na reconciliação (foram 84, sendo 71 em jobs ativos de produção
que sempre funcionaram), e na Fase 2 levaria a migração de segredos a procurar no vault
credencial que nunca existiu aqui. O catálogo guarda os dois separados —
`job_connection_alias` para alias, `remote_targets` para destino.

Consequência de arquitetura: **todo envio a cliente passa por uma única conexão**. Isso
concentra o raio de alcance da credencial do jump SFTP e é o que torna o IP de egress um
ponto único na migração da Fase 2.

## Semântica de operação (vira API)

| Flag legada | Significado | Exposição na plataforma |
|---|---|---|
| `--process-file X` | Qual contrato executar | `job_id` do catálogo |
| `--manual-steps Y` | Executa subconjunto de steps | Campo `steps` na requisição de execução |
| `--dates-pattern-files Z` | Data(s)-alvo do processamento | Campo `dates_pattern`; múltiplas datas = serialização sequencial (nunca paralelizar por processo — lição do SOP Zinli) |
| `--validate-file` | Valida contrato sem executar | Pré-validação obrigatória antes de execução manual; Fase 3: validação contínua do catálogo |
| `--no-mail` | Suprime notificação | Opção de execução |

## Padrões de referência para testes de paridade (Executor v3)

- **Fechamento Base2**: contrato com `stop_on_failed=true` — falha em qualquer step interrompe.
- **MFT bulk Sodexo**: 78 steps tolerantes a falha — steps continuam mesmo com falhas individuais.

A suíte de testes de contrato deve ser construída a partir dos padrões do inventário e rodar contra ambos os executores.
