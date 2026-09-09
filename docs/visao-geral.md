# Visão Geral

## Estado atual (As-Is)

- Framework shell (`main.sh`) disparado por cron, usuário `batch_user`, host `com-ins-bch-mdw-dtu-1` (+ `Batch-Prod-srv-sftp2-120`).
- **509 entradas** no crontab: 390 ativas, 119 desabilitadas/on-demand.
- Distribuição por domínio (diretórios do framework):

| Domínio | Diretório | Total | Ativos | Desab./On-demand |
|---|---|---:|---:|---:|
| Emissão (cartões físicos/virtuais) | `/emisiones` | 47 | 30 | 17 |
| Utilitários e rotinas | `/otros` | 68 | 29 | 39 |
| Relatórios (DRM, conciliações) | `/reportes` | 37 | 25 | 12 |
| Transações e liquidação | `/transacciones` | 29 | 24 | 5 |
| Embossing (Thales, Idemia, Oberthur) | `/emboces` | 22 | 19 | 3 |
| Saldos (USD e moedas locais) | `/saldos` | 19 | 17 | 2 |
| Base II / Clearing (Visa/MC) | `/base2` | 13 | 9 | 4 |

- O crontab mistura ambientes (PROD, UAT, TEST, DEV) no mesmo host/usuário e acumula três papéis que a arquitetura-alvo separa: *scheduler*, *feature flag* (entradas comentadas) e *documentação* (comentários com racional).
- Rotinas de manutenção a cada 15 min: `clean_empty_folders.sh`, `validate_connections_json.sh`.

## Engine legado (confirmado por engenharia reversa)

- Ponto de entrada único: `main.sh` com contratos JSON parametrizados.
- Steps sequenciais com `stop_on_failed` por step.
- Funções suportadas: `download`/`upload` (SFTP, S3, Azure), `execute_command` (SSH local ou remoto), `download_remote`/`upload_remote` (via jump server), `encrypt`/`decrypt` (GPG), `copy_local`, `send_mail`.
- Variáveis de data dinâmicas: `@@@YYYY@@@`, `@@@JULIANO@@@`, `@@@YYYYMMDD@@@`.
- Flags de operação controlada: `--manual-steps`, `--dates-pattern-files`, `--validate-file`, `--no-mail`.
- Reprocessamento hoje é manual (SOP Zinli/MFTech): SSH no servidor, `nohup` com comando completo, laço `for` sequencial para intervalos de datas — o próprio SOP alerta que paralelismo causa timeout e contenção.

## O que preservar

1. **Contrato JSON declarativo** — separa "o que fazer" (steps) de "como executar" (engine); permite trocar o executor sem alterar os 509 contratos.
2. **Credenciais centralizadas em `connections.json` com aliases** — contratos não contêm senhas/IPs; migrar para vault troca só a implementação do dicionário.
3. **Semântica de reprocessamento** por data (`--dates-pattern-files`) e por step (`--manual-steps`) — já validada em produção; é exatamente o que Back Office e orchestrator devem expor.

## Riscos que motivam a evolução

- **Dependência de servidor**: agendamento, execução, credenciais, logs e estado no mesmo host.
- **SSH compartilhado**: contas `batch_user`/`root`, sem RBAC nem trilha de auditoria — evidência de reprocessamento é registrada manualmente.
- **Mistura de ambientes e falta de visibilidade**: sem visão de dependências, janelas ou SLAs; job que *não rodou* é invisível (observabilidade atual detecta erro em log, não execução ausente).
- **Contenção de recursos e segredos em texto plano**: JARs com heap fixo de 2 GB por execução; segredos em disco protegidos só por permissão de filesystem.
