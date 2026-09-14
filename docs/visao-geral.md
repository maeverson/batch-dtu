# Visão Geral

## Estado atual (As-Is)

- Framework shell (`main.sh`) disparado por cron, usuário `batch_user`. O mesmo framework roda nos dois hosts em escopo, a partir de `/opt2/batch_v2/batch-commons-framework`.
- **Escopo: dois hosts**, ambos coletados em 09/2026:

| Host | Endereço | SO / timezone | Jobs | Ambientes |
|---|---|---|---:|---|
| `Batch-Prod-srv-sftp-2-120` (`srv-sftp-2`) | `172.17.37.120` | CentOS 7 ELS / `America/Lima` | 597 (393 ativos) | PROD 597 |
| `Batch-DTU` (`com-ins-bch-mdw-dtu-1`) | `172.21.86.76` | Amazon Linux 2023 / `America/Bogota` | 540 (362 ativos) | UAT 378, TEST 159, **PROD 2**, DEV 1 |

  Os servidores PROD `p-batch-1`, `reportes-130`, `P-MDW-BATCH-1`, `p-mx-batch-1` e `P-MX-SFTP-2` têm jobs e estão **fora do escopo**. Nenhum número aqui descreve o parque total da empresa.
Em **PROD** (`172.17.37.120`), que é o alvo da migração da Fase 2:

- **597 linhas de job** no crontab: 393 ativas, 204 desabilitadas/on-demand — **527 jobs distintos** após dedup por wrapper+contrato (a premissa inicial de 509/390/119 estava defasada).
- **575 contratos JSON** em disco, dos quais 58 órfãos (nenhum job os referencia).

Em **UAT** (`172.21.86.76`): 540 linhas de job (362 ativas, 178 desabilitadas), 587 contratos em disco com 80 órfãos, 76 aliases declarados. Crontab bem menor em prosa e manutenção: 833 linhas contra 1352.
- Distribuição por domínio (diretórios do framework):

| Domínio | Diretório | Total | Ativos | Desab./On-demand | Contratos distintos |
|---|---|---:|---:|---:|---:|
| Utilitários e rotinas | `/otros` | 232 | 138 | 94 | 200 |
| Relatórios (DRM, conciliações) | `/reportes` | 124 | 80 | 44 | 104 |
| Transações e liquidação | `/transacciones` | 63 | 51 | 12 | 61 |
| Emissão (cartões físicos/virtuais) | `/emisiones` | 56 | 36 | 20 | 46 |
| Embossing (Thales, Idemia, Oberthur) | `/emboces` | 54 | 36 | 18 | 47 |
| Base II / Clearing (Visa/MC) | `/base2` | 42 | 34 | 8 | 41 |
| Saldos (USD e moedas locais) | `/saldos` | 26 | 18 | 8 | 23 |
| **Total** | | **597** | **393** | **204** | **522** |

> Linhas de job do crontab, coleta de 09/2026 (`seed/raw/reports/import-srv-sftp-2.md`). A ordem por volume — `/otros` sozinho é 39% do host — é a que define as waves da Fase 2.

- **O host de produção é puro PROD**: 597 de 597 jobs (medido). O único artefato `uat_*` nele é um contrato órfão, que nenhum job referencia.
- **O host de UAT mistura ambientes**: UAT 378, TEST 159, **PROD 2**, DEV 1. É lá que a premissa original — "o crontab mistura PROD/UAT/TEST/DEV" — se confirma; ela estava atribuída ao host errado. Há 12 jobs cujo nome declara um ambiente e cujo contrato declara outro (`dimensao-divergente`), e o contrato é a fonte autoritativa.
- O crontab acumula três papéis que a arquitetura-alvo separa: *scheduler*, *feature flag* (entradas comentadas) e *documentação* (comentários com racional).
- Rotinas de manutenção a cada 15 min: `clean_empty_folders.sh`, `validate_connections_json.sh`.

## Engine legado (confirmado por engenharia reversa)

- Ponto de entrada único: `main.sh` com contratos JSON parametrizados.
- Steps sequenciais com `stop_on_failed` por step.
- Funções suportadas: `download`/`upload` (SFTP, S3, Azure), `execute_command` (SSH local ou remoto), `download_remote`/`upload_remote` (via jump server), `encrypt`/`decrypt` (GPG), `copy_local`, `send_mail`.
- Variáveis de data dinâmicas: `@@@YYYY@@@`, `@@@JULIANO@@@`, `@@@YYYYMMDD@@@`.
- Flags de operação controlada: `--manual-steps`, `--dates-pattern-files`, `--validate-file`, `--no-mail`.
- Reprocessamento hoje é manual (SOP Zinli/MFTech): SSH no servidor, `nohup` com comando completo, laço `for` sequencial para intervalos de datas — o próprio SOP alerta que paralelismo causa timeout e contenção.

## O que preservar

1. **Contrato JSON declarativo** — separa "o que fazer" (steps) de "como executar" (engine); permite trocar o executor sem alterar os 575 contratos.
2. **Credenciais centralizadas em `connections.json` com aliases** — contratos não contêm senhas/IPs; migrar para vault troca só a implementação do dicionário.
3. **Semântica de reprocessamento** por data (`--dates-pattern-files`) e por step (`--manual-steps`) — já validada em produção; é exatamente o que Back Office e orchestrator devem expor.

## Riscos que motivam a evolução

- **Dependência de servidor**: agendamento, execução, credenciais, logs e estado no mesmo host.
- **SSH compartilhado**: contas `batch_user`/`root`, sem RBAC nem trilha de auditoria — evidência de reprocessamento é registrada manualmente.
- **Mistura de ambientes e falta de visibilidade**: sem visão de dependências, janelas ou SLAs; job que *não rodou* é invisível (observabilidade atual detecta erro em log, não execução ausente).
- **Contenção de recursos e segredos em texto plano**: JARs com heap fixo de 2 GB por execução; segredos em disco protegidos só por permissão de filesystem.
