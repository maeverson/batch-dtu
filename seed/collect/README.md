# Coleta do inventário — insumo do seed do `job-catalog`

Um script, um artefato. [collect-seed.sh](collect-seed.sh) roda **dentro da sessão SSH** do servidor,
é **read-only** sobre o parque, e entrega **um único `tar.gz`** para download via sftp/scp.

| Servidor | Situação |
|---|---|
| `srv-sftp-2` = `Batch-Prod-srv-sftp-2-120` = `172.17.37.120` | **é o host em escopo**: `batch_user` com 597 linhas de job → 527 jobs distintos em `/opt2/batch_v2/batch-commons-framework/schedulers/`, contratos em `.../process_files/<cliente>_<país>/`. CentOS 7 ELS, TZ `America/Lima` |
| `p-ins-rep-mdw-1` | sem batch: crontab do `batch_user` vazio, só agente ManageEngine e timers de sistema |
| `uat-sftp-srv-1` | sem batch e sem binário `crontab` no PATH |
| `com-ins-bch-mdw-dtu-1` | **resolvido em 11/09/2026**: é o hostname do `Batch-DTU`, o host de UAT (linha acima). O doc de arquitetura acertou o hostname e errou o papel — atribuiu a ele o parque de produção, que roda em `srv-sftp-2` / `172.17.37.120` |
| outros servidores de batch PROD | `p-batch-1` (`172.17.37.66`), `reportes-130` (`172.24.6.130`), `P-MDW-BATCH-1`, `p-mx-batch-1`, `P-MX-SFTP-2` — **têm jobs agendados e estão fora do escopo**; nunca foram coletados |
| `Batch-DTU` = `com-ins-bch-mdw-dtu-1` = `172.21.86.76` | **host de UAT**, **em escopo** — coletado em 11/09/2026: 540 linhas de job, 587 contratos, Amazon Linux 2023, TZ `America/Bogota`. **Mistura ambientes**: UAT 378, TEST 159, PROD 2, DEV 1 |

> A arquitetura real tem **uma camada a mais** que o doc: o cron chama um wrapper por job
> (`schedulers/<domínio>/<nome>.sh`) e é **dentro do wrapper** que `main.sh --process-file` aparece.
> São dois eixos: `domain` vem do caminho do wrapper, `client`/`country` do caminho do contrato.

> **Acesso SSH humano ao host de execução é break-glass** (`docs/seguranca.md`): sessão autorizada,
> gravada e revisada depois. Registre a janela e o ticket junto ao pacote coletado.

---

## Passo 1 — abrir a sessão e virar root

```bash
ssh -t mwaitman0@<ip-do-servidor>
sudo -i
```

Sem root, o script tenta `sudo -n` e, se não conseguir, segue **parcial** e registra
`no_privilege 1` em `HOST-INFO` (crontabs de outros usuários e arquivos do `batch_user` podem faltar).

## Passo 2 — colar o script

Copie o conteúdo de [collect-seed.sh](collect-seed.sh) dentro deste envelope:

```bash
set +H
cat > /tmp/collect-seed.sh <<'FIM_DO_SCRIPT'
<<< cole aqui todo o conteúdo de seed/collect/collect-seed.sh >>>
FIM_DO_SCRIPT
```

`set +H` desliga a expansão de histórico — sem isso o `#!` da primeira linha vira
`event not found` no meio do paste. É o único cuidado do procedimento.

Para gerar envelope + script prontos na sua máquina:

```bash
{ echo 'set +H'; echo "cat > /tmp/collect-seed.sh <<'FIM_DO_SCRIPT'"; \
  cat seed/collect/collect-seed.sh; echo 'FIM_DO_SCRIPT'; } | tee /dev/stdout | \
  { command -v xclip >/dev/null && xclip -selection clipboard || cat >/dev/null; }
```

Se `scp` de subida funcionar, é equivalente:
`scp seed/collect/collect-seed.sh mwaitman0@<ip>:/tmp/collect-seed.sh`

## Passo 3 — executar

```bash
bash /tmp/collect-seed.sh
```

No fim ele imprime o caminho do `tar.gz`, o `sha256`, os caminhos detectados e o censo de cron.

Ajustes, se necessário:

```bash
FW_ROOT=/opt2/batch_v2/batch-commons-framework bash /tmp/collect-seed.sh   # forçar a raiz
PROC_DIR=/opt2/batch_v2/batch-commons-framework/process_files bash /tmp/collect-seed.sh
WITH_FILES=0  bash /tmp/collect-seed.sh    # só metadados, sem copiar contratos/wrappers
WITH_ENGINE=0 bash /tmp/collect-seed.sh    # sem o fonte do engine
WITH_LOGS=1   bash /tmp/collect-seed.sh    # inclui nomes de arquivos de log (sem conteúdo)
MAX_MB=1000   bash /tmp/collect-seed.sh    # teto da cópia (default 500MB; acima disso ela é abortada)
```

Se `processes_dir` vier vazio em `DETECTED-PATHS`, rode de novo com `PROC_DIR=` (**caminho
absoluto** — caminho relativo é rejeitado de propósito).

## Passo 4 — baixar e limpar

```bash
# na sua máquina
cd /home/maeverson/AMBIENTE/repositórios/batch-dtu
sftp mwaitman0@<ip>:/tmp/batch-seed-*.tar.gz seed/raw/
sha256sum seed/raw/batch-seed-*.tar.gz     # compare com o impresso no servidor
```

```bash
# de volta no servidor
rm -f /tmp/batch-seed-*.tar.gz /tmp/collect-seed.sh
exit
```

## Passo 5 — abrir localmente

```bash
tar -xzf seed/raw/batch-seed-<host>-<stamp>.tar.gz -C seed/raw/
( cd seed/raw/batch-seed-<host>-<stamp> && sha256sum -c SHA256SUMS --quiet )
python3 seed/collect/unpack_bundle.py seed/raw/batch-seed-<host>-<stamp>/inventory.txt
```

O `unpack_bundle.py` explode o `inventory.txt` em TSVs por assunto em `seed/raw/<host>/`.
Os arquivos do framework já vêm extraídos em `seed/raw/batch-seed-<host>-<stamp>/framework/`.

---

## O que o pacote contém

```
batch-seed-<host>-<stamp>/
  inventory.txt        seções "##### BEGIN <NOME> | chave=valor" / "##### END <NOME>"
  framework/           contratos JSON, wrappers e fonte do engine (árvore relativa à raiz)
  SHA256SUMS
  LEIA-ME.txt
```

Seções do `inventory.txt` (cada uma vira um arquivo no `unpack_bundle.py`):

| Seção | Conteúdo | Serve para |
|---|---|---|
| `HOST-INFO` | host, ator, **timezone** (o crontab não declara), uname | campo `timezone` do `job` |
| `CRONTAB` (uma por fonte) | crontab bruto por usuário + `/etc/crontab` + `/etc/cron.d`, comentários preservados | seed e `status_reason` |
| `CRON-SUMMARY` | censo por linha: ativas, desabilitadas, prosa, `VAR=`, scripts distintos | conferência 393/204/527 |
| `DOMAIN-CENSUS`, `DOMAIN-DISTINCT-SCRIPTS` | por domínio (`/schedulers/<dom>/`) | `domain` do `job` |
| `CLIENT-CENSUS`, `CLIENT-DIRS` | por `<cliente>_<país>` (`/process_files/<dir>/`) | `client`/`country` |
| `WRAPPER-INVOCATION` | **wrapper → flags do `main.sh`** (`--process-file`, `--manual-steps`, `--dates-pattern-files`, `--no-mail`, `--validate-file`) | é o elo cron→contrato e os parâmetros hoje hardcoded |
| `CONTRACTS-INDEX`, `SCHEDULERS-INDEX` | path, bytes, mtime, owner, modo | `contract_hash`, dedupe |
| `ORPHAN-CONTRACTS` | contrato em disco que nenhum wrapper referencia | reconciliação |
| `BROKEN-REFS` | wrapper aponta para contrato inexistente | reconciliação |
| `UNRESOLVED-REFS` | ref com variável não expandida (`$PROC/...`) | precisa leitura manual do wrapper |
| `PATHOLOGY-NAMES` | nome com espaço no início/fim, tab ou byte não-ASCII | ex.: `banrural_guatemala ` |
| `CASE-COLLISIONS` | diretórios que colidem sem distinção de caixa | ex.: `ontop_peru`/`Ontop_peru` |
| `CONNECTION-ALIASES` | `name type host user port region auth key_path` — **sem senhas** | `connection_alias` |
| `ALIASES-USED-IN-CONTRACTS` | alias citado nos contratos, com contagem | cruzar com o `connections.json` |
| `FRAMEWORK-LISTING`, `FRAMEWORK-SHA256` | inventário e hashes do framework | divergência de versão entre hosts |
| `SECRET-SCAN` | `path:linha:palavra-chave` (**sem valores**) | revisão antes de qualquer commit |

## O que o script NÃO faz

- **Não copia `connections.json`** — lê por pipe e emite só metadado por alias (aceita os dois
  formatos: array de objetos com `name`, o real, e o indexado por alias). **Senha nunca é emitida.**
- **Não executa nada do framework legado** — nem `main.sh --help`. Só leitura de arquivos.
- **Não altera nem remove nada** do parque: escreve apenas em `OUT_DIR` (default `/tmp`).
- **Não varre a partir do diretório corrente**: todo caminho é absoluto e verificado antes.
- **Não coleta conteúdo de log** — só nomes de arquivo, e apenas com `WITH_LOGS=1`.

## Antes de qualquer commit

`seed/raw/` está no `.gitignore` e deve continuar assim: contratos carregam nomes de cliente,
IPs internos e caminhos internos.

Se `SECRET-SCAN` vier **não vazio**, é achado de segurança real — contrato violando o invariante 5.
Escale ao owner do domínio; não "limpe" o contrato (invariante 1: contratos não são reescritos).

## Conferência imediata

1. `cron-summary.tsv` → `ativas` / `desabilitadas_com_comando` / `scripts_distintos` contra
   **393 / 204 / 527** (baseline do pacote de 10/09/2026 no `srv-sftp-2`). A premissa
   original era 390 / 119 / 509; a coleta de 09/09 já media 393 / 204 / 526.
2. `orphan-contracts.txt` e `broken-refs.txt` → contrato sem job e job sem contrato.
3. `pathology-names.txt` e `case-collisions.txt` → nomes que quebram em migração.
4. `unresolved-refs.txt` → wrappers que montam o caminho por variável (leitura manual).
5. `unpack-report.txt` → algum `ERRO`/`AVISO` no desempacotamento.
