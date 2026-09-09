# Contrato JSON — Interface Estável

**Este é o ativo mais valioso do sistema atual.** Todos os módulos que interpretam ou geram contratos devem tratá-lo como interface pública versionada.

## Regras

- Extensões são **sempre aditivas** e controladas por `schema_version`.
- Contratos **nunca** contêm senhas, IPs ou segredos — apenas *aliases* de conexão (resolvidos via `connections.json` no legado; via vault a partir da Fase 2).
- O executor (legado `main.sh` ou Executor v3) interpreta o mesmo contrato; a paridade é verificada por testes de contrato.

## Semântica preservada obrigatoriamente

| Recurso | Semântica |
|---|---|
| Steps sequenciais | Executados em ordem; `stop_on_failed` por step decide se falha interrompe o job |
| `download` / `upload` | Transferência SFTP, S3 ou Azure Blob |
| `execute_command` | Comando local ou SSH remoto (a partir da Fase 2: restrito a hosts/scripts registrados no catálogo) |
| `download_remote` / `upload_remote` | Transferência via jump server; `upload_remote` = envio a cliente (step sensível) |
| `encrypt` / `decrypt` | GPG; chaves de cliente com validade monitorada (expiração é modo de falha conhecido) |
| `copy_local` | Cópia local de arquivos |
| `send_mail` | Notificação por e-mail (`send_infra_mail` incluída) |
| Placeholders de data | `@@@YYYY@@@`, `@@@JULIANO@@@`, `@@@YYYYMMDD@@@` (e demais do framework), resolvidos contra a data-alvo da execução |

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
