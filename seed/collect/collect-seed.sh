#!/usr/bin/env bash
#
# collect-seed.sh v2.0.1 — RODAR NO SERVIDOR LEGADO, dentro da sessao SSH.
#
# Coleta READ-ONLY de tudo o que o seed do job-catalog precisa e entrega UM
# UNICO tar.gz para download via sftp/scp.
#
# Uso (como root):
#   sudo -i
#   bash /tmp/collect-seed.sh
#
# Saida:  /tmp/batch-seed-<host>-<stamp>.tar.gz  contendo:
#   batch-seed-<host>-<stamp>/
#     inventory.txt      metadados em secoes delimitadas (crontabs, censos, aliases,
#                        indices, mapa wrapper->contrato, patologias, referencias cruzadas)
#     framework/...      contratos JSON, wrappers de scheduler e fonte do engine,
#                        com a arvore relativa a raiz do framework preservada
#     SHA256SUMS         integridade de tudo acima
#     LEIA-ME.txt        o que fazer com o pacote
#
# Variaveis de ambiente (todas opcionais):
#   FW_ROOT=/path      raiz do framework (default: detectada pelo crontab via /schedulers/)
#   PROC_DIR=/path     diretorio de contratos (default: <fw>/process_files, senao <fw>/processes)
#   CONNECTIONS_FILE   caminho do connections.json (default: procurado sob a raiz)
#   WITH_FILES=1|0     copiar contratos/wrappers/engine (default 1)
#   WITH_ENGINE=1|0    incluir os .sh da raiz do framework (default 1)
#   WITH_LOGS=0|1      incluir nomes de arquivos de log, sem conteudo (default 0)
#   MAX_MB=500         teto de tamanho para a copia de arquivos (default 500)
#   OUT_DIR=/tmp       onde gravar o tar.gz (default /tmp)
#
# GARANTIAS
#   - Somente leitura do parque: nada e criado, alterado ou removido fora de OUT_DIR.
#   - Nenhum comando do framework legado e executado (nem main.sh --help).
#   - connections.json NUNCA e copiado. Sai apenas metadado por alias: name, type, host,
#     user, port, region, metodo de auth (key|password|iam|none) e caminho da chave.
#     Valor de senha jamais e emitido — so o fato de o campo existir.
#   - Toda varredura parte de caminho ABSOLUTO verificado; nunca do diretorio corrente.
#   - Nomes com espaco/caractere estranho sao tratados com -print0 e aspas.
#
VERSION="2.0.1"
WITH_FILES=${WITH_FILES:-1}
WITH_ENGINE=${WITH_ENGINE:-1}
WITH_LOGS=${WITH_LOGS:-0}
MAX_MB=${MAX_MB:-500}
FW_ROOT=${FW_ROOT:-}
PROC_DIR=${PROC_DIR:-}
OUT_DIR=${OUT_DIR:-/tmp}

HOST=$(hostname -s 2>/dev/null || echo unknown-host)
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
NAME="batch-seed-$HOST-$STAMP"

msg() { printf '[seed] %s\n' "$*" >&2; }

mkdir -p "$OUT_DIR" 2>/dev/null
WORK=$(mktemp -d "$OUT_DIR/.batch-seed.XXXXXX") || { msg "falha ao criar temporario em $OUT_DIR"; exit 1; }
STAGE="$WORK/$NAME"
mkdir -p "$STAGE/framework" || exit 1
INV="$STAGE/inventory.txt"
: > "$INV"

CS="$WORK/cron-sources"          # todas as linhas de cron, sem cabecalhos
SCRIPTS="$WORK/cron-scripts"     # scripts .sh referenciados pelo cron
TARGETS="$WORK/job-scripts"      # wrappers em disco + scripts do cron que existem
PROC_REFS="$WORK/process-refs"   # --process-file resolvidos
DISK_CONTRACTS="$WORK/disk-contracts"
CONNTMP="$WORK/conn"
: > "$CS"; : > "$SCRIPTS"; : > "$TARGETS"; : > "$PROC_REFS"; : > "$DISK_CONTRACTS"

# Privilegio: root nao precisa de nada; fora disso tenta sudo nao-interativo.
NOPRIV=0
if [ "$(id -u)" = "0" ]; then
  SUDO=""
elif sudo -n true 2>/dev/null; then
  SUDO="sudo -n"
else
  SUDO=""
  NOPRIV=1
fi

CRONTAB_BIN=$(command -v crontab 2>/dev/null)
[ -n "$CRONTAB_BIN" ] || CRONTAB_BIN=/usr/bin/crontab

sec() { printf '\n##### BEGIN %s\n' "$*" >> "$INV"; }
end() { printf '##### END %s\n' "$1" >> "$INV"; }

# run <SECAO> <comando...>
run() {
  n="$1"; shift
  sec "$n"
  "$@" >> "$INV" 2>>"$INV" || printf '(comando falhou: %s)\n' "$*" >> "$INV"
  end "$n"
}

# absdir <path> -> verdadeiro so se for caminho ABSOLUTO e diretorio existente
absdir() {
  case "$1" in
    /*) $SUDO test -d "$1" ;;
    *)  return 1 ;;
  esac
}

msg "host=$HOST  versao=$VERSION"
[ "$NOPRIV" = "1" ] && msg "AVISO sem root e sem sudo nao-interativo: a coleta pode ficar PARCIAL"

# ---------------------------------------------------------------------------
# 1. Identidade do host e TIMEZONE (o crontab nao declara TZ)
# ---------------------------------------------------------------------------
sec "HOST-INFO"
{
  printf 'hostname_short\t%s\n'   "$HOST"
  printf 'hostname_fqdn\t%s\n'    "$(hostname -f 2>/dev/null || echo "$HOST")"
  printf 'collected_at_utc\t%s\n' "$STAMP"
  printf 'collected_by\t%s\n'     "$(id -un 2>/dev/null)"
  printf 'effective_uid\t%s\n'    "$(id -u 2>/dev/null)"
  printf 'no_privilege\t%s\n'     "$NOPRIV"
  printf 'date_local\t%s\n'       "$(date +'%Y-%m-%dT%H:%M:%S%z %Z' 2>/dev/null)"
  printf 'etc_localtime\t%s\n'    "$(readlink -f /etc/localtime 2>/dev/null)"
  printf 'etc_timezone\t%s\n'     "$(cat /etc/timezone 2>/dev/null)"
  printf 'uname\t%s\n'            "$(uname -a 2>/dev/null)"
  printf 'crontab_bin\t%s\n'      "$CRONTAB_BIN"
  printf 'collector\t%s\n'        "collect-seed.sh $VERSION"
} >> "$INV"
end "HOST-INFO"

run "OS-RELEASE"  cat /etc/os-release
run "TIMEDATECTL" timedatectl

# ---------------------------------------------------------------------------
# 2. Todas as fontes de agendamento
# ---------------------------------------------------------------------------
ME=$(id -un 2>/dev/null || echo current)
sec "CRONTAB | user=$ME | source=crontab -l"
"$CRONTAB_BIN" -l >> "$INV" 2>>"$INV" || printf '(sem crontab para %s)\n' "$ME" >> "$INV"
end "CRONTAB"
"$CRONTAB_BIN" -l >> "$CS" 2>/dev/null

for spool in /var/spool/cron /var/spool/cron/crontabs; do
  [ -d "$spool" ] || continue
  for path in "$spool"/*; do
    $SUDO test -f "$path" || continue
    u=$(basename "$path")
    sec "CRONTAB | user=$u | source=$path"
    $SUDO cat "$path" >> "$INV" 2>>"$INV"
    end "CRONTAB"
    $SUDO cat "$path" >> "$CS" 2>/dev/null
  done
done

sec "CRONTAB | user=system | source=/etc/crontab"
$SUDO cat /etc/crontab >> "$INV" 2>>"$INV"
end "CRONTAB"
$SUDO cat /etc/crontab >> "$CS" 2>/dev/null

for f in /etc/cron.d/*; do
  [ -f "$f" ] || continue
  sec "CRONTAB | user=system | source=$f"
  $SUDO cat "$f" >> "$INV" 2>>"$INV"
  end "CRONTAB"
  $SUDO cat "$f" >> "$CS" 2>/dev/null
done

run "CRON-PERIODIC-LISTING" ls -la /etc/cron.hourly /etc/cron.daily /etc/cron.weekly /etc/cron.monthly
run "SYSTEMD-TIMERS"        systemctl list-timers --all --no-pager
run "CRON-ALLOW-DENY"       cat /etc/cron.allow /etc/cron.deny

sec "CRON-TZ-DECLARATIONS"
grep -nE '^[[:space:]]*(CRON_TZ|TZ)=' "$CS" >> "$INV" 2>/dev/null
end "CRON-TZ-DECLARATIONS"

# ---------------------------------------------------------------------------
# 3. Descoberta nivel 1 — scripts referenciados pelo cron
# ---------------------------------------------------------------------------
# Primeiro caminho absoluto .sh de cada linha (o comando, nao o redirect de log).
awk '{ if (match($0, /\/[A-Za-z0-9_@.\/-]+\.sh/)) print substr($0, RSTART, RLENGTH) }' "$CS" \
  | sort -u > "$SCRIPTS"

if [ -z "$FW_ROOT" ]; then
  FW_ROOT=$(sed -n 's|\(/.*\)/schedulers/.*|\1|p' "$SCRIPTS" | sort | uniq -c | sort -rn | head -1 | sed -E 's/^ *[0-9]+ *//')
fi
absdir "$FW_ROOT" || FW_ROOT=""

SCHED_DIR=""
absdir "$FW_ROOT/schedulers" && SCHED_DIR="$FW_ROOT/schedulers"

sec "CRON-REFERENCED-SCRIPTS | formato=existe path"
while IFS= read -r p; do
  if $SUDO test -f "$p"; then printf 'sim\t%s\n' "$p" >> "$INV"; else printf 'NAO\t%s\n' "$p" >> "$INV"; fi
done < "$SCRIPTS"
end "CRON-REFERENCED-SCRIPTS"

# ---------------------------------------------------------------------------
# 4. Censo de linhas de cron (conferencia contra 509 / 390 / 119)
#    Conta por LINHA, nao por ocorrencia.
# ---------------------------------------------------------------------------
TOT=$(wc -l < "$CS" | tr -d ' ')
BLANK=$(grep -cE '^[[:space:]]*$' "$CS")
ENVV=$(grep -cE '^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*=' "$CS")
ACT=$(grep -vE '^[[:space:]]*(#|$)' "$CS" | grep -vcE '^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*=')
COMM=$(grep -cE '^[[:space:]]*#' "$CS")
DIS=$(grep -E '^[[:space:]]*#' "$CS" | grep -cE '(\.sh|\.py|\.jar|/bin/)')
PROSE=$((COMM - DIS))
ACT_SCHED=$(grep -vE '^[[:space:]]*(#|$)' "$CS" | grep -c '/schedulers/')
DIS_SCHED=$(grep -E '^[[:space:]]*#' "$CS" | grep -c '/schedulers/')
DISTINCT=$(wc -l < "$SCRIPTS" | tr -d ' ')

sec "CRON-SUMMARY"
{
  printf 'linhas_totais\t%s\n'                   "$TOT"
  printf 'linhas_vazias\t%s\n'                   "$BLANK"
  printf 'atribuicoes_ambiente\t%s\n'            "$ENVV"
  printf 'comentario_documentacao\t%s\n'         "$PROSE"
  printf 'ativas\t%s\n'                          "$ACT"
  printf 'desabilitadas_com_comando\t%s\n'       "$DIS"
  printf 'ativas_em_schedulers\t%s\n'            "$ACT_SCHED"
  printf 'desabilitadas_em_schedulers\t%s\n'     "$DIS_SCHED"
  printf 'scripts_distintos\t%s\n'               "$DISTINCT"
  printf 'CANDIDATO_ativas_390\t%s\n'            "$ACT"
  printf 'CANDIDATO_desabilitadas_119\t%s\n'     "$DIS"
  printf 'CANDIDATO_total_509\t%s\n'             "$((ACT + DIS))"
  printf 'CANDIDATO_509_scripts_distintos\t%s\n' "$DISTINCT"
} >> "$INV"
end "CRON-SUMMARY"

# Dominio vem de /schedulers/<dominio>/; cliente_pais vem de /process_files/<cliente_pais>/
census_domain() {
  awk '{ if (match($0, /\/schedulers\/[^\/]+\//)) { s=substr($0,RSTART,RLENGTH); sub(/^\/schedulers\//,"",s); sub(/\/$/,"",s); print s } }'
}
census_client() {
  awk '{ if (match($0, /\/(process_files|processes)\/[^\/]+\//)) { s=substr($0,RSTART,RLENGTH); sub(/^\/(process_files|processes)\//,"",s); sub(/\/$/,"",s); print s } }'
}

sec "DOMAIN-CENSUS | formato=qtd situacao dominio | fonte=linhas de cron"
{
  grep -vE '^[[:space:]]*(#|$)' "$CS" | census_domain | sort | uniq -c | sed -E 's/^ *([0-9]+) *(.*)/\1\tativo\t\2/'
  grep -E '^[[:space:]]*#' "$CS"      | census_domain | sort | uniq -c | sed -E 's/^ *([0-9]+) *(.*)/\1\tdesabilitado\t\2/'
} >> "$INV" 2>/dev/null
end "DOMAIN-CENSUS"

sec "DOMAIN-DISTINCT-SCRIPTS | formato=qtd dominio"
census_domain < "$SCRIPTS" | sort | uniq -c | sed -E 's/^ *([0-9]+) *(.*)/\1\t\2/' >> "$INV" 2>/dev/null
end "DOMAIN-DISTINCT-SCRIPTS"

# ---------------------------------------------------------------------------
# 5. Descoberta nivel 2 — ler os wrappers para achar main.sh e os contratos
# ---------------------------------------------------------------------------
if [ -n "$SCHED_DIR" ]; then
  $SUDO find "$SCHED_DIR" -maxdepth 4 -type f -name '*.sh' 2>/dev/null >> "$TARGETS"
fi
while IFS= read -r p; do
  $SUDO test -f "$p" && printf '%s\n' "$p" >> "$TARGETS"
done < "$SCRIPTS"
sort -u "$TARGETS" -o "$TARGETS"
NWRAP=$(wc -l < "$TARGETS" | tr -d ' ')
msg "scripts de job localizados: $NWRAP"

MAIN_SH=""
if [ -s "$TARGETS" ]; then
  MAIN_SH=$($SUDO xargs -a "$TARGETS" -d '\n' -r grep -hoE '/[A-Za-z0-9_./-]*main\.sh' 2>/dev/null \
    | sort | uniq -c | sort -rn | head -1 | sed -E 's/^ *[0-9]+ *//')
fi
[ -n "$MAIN_SH" ] || MAIN_SH=$(grep -hoE '/[A-Za-z0-9_./-]*main\.sh' "$CS" 2>/dev/null | sort | uniq -c | sort -rn | head -1 | sed -E 's/^ *[0-9]+ *//')

# Invocacoes dentro dos wrappers: flags do main.sh, com procedencia por arquivo
sec "WRAPPER-INVOCATION | formato=wrapper flag_e_valor | fonte=grep nos wrappers"
if [ -s "$TARGETS" ]; then
  $SUDO xargs -a "$TARGETS" -d '\n' -r grep -oHE -- '--(process-file|manual-steps|dates-pattern-files|validate-file|no-mail)([=[:space:]]+("[^"]*"|[^[:space:];|&]+))?' 2>/dev/null \
    | sed -E 's/:--/\t--/' >> "$INV"
fi
end "WRAPPER-INVOCATION"

# Referencias a contrato: resolvidas x com variavel nao expandida.
# O valor e aceito entre aspas duplas (caminho com espaco) ou sem aspas. Caminho entre
# aspas SIMPLES nao e reconhecido e aparecera em BROKEN-REFS — visivel, nunca silencioso.
if [ -s "$TARGETS" ]; then
  $SUDO xargs -a "$TARGETS" -d '\n' -r grep -hoE -- '--process-file[=[:space:]]+("[^"]*"|[^[:space:];|&]+)' 2>/dev/null \
    | sed -E 's/^--process-file[=[:space:]]+//; s/^"//; s/"$//' | sort -u > "$WORK/allrefs"
else
  : > "$WORK/allrefs"
fi
grep -hoE -- '--process-file[=[:space:]]+("[^"]*"|[^[:space:];|&]+)' "$CS" 2>/dev/null \
  | sed -E 's/^--process-file[=[:space:]]+//; s/^"//; s/"$//' >> "$WORK/allrefs"
sort -u "$WORK/allrefs" -o "$WORK/allrefs"
grep -v '\$' "$WORK/allrefs" | grep '^/' > "$PROC_REFS"

sec "UNRESOLVED-REFS | formato=ref com variavel nao expandida"
grep '\$' "$WORK/allrefs" >> "$INV" 2>/dev/null
end "UNRESOLVED-REFS"

if [ -z "$PROC_DIR" ]; then
  PROC_DIR=$(sed -E 's|(.*/process_files)/.*|\1|; s|(.*/processes)/.*|\1|' "$PROC_REFS" 2>/dev/null \
    | grep '^/' | sort | uniq -c | sort -rn | head -1 | sed -E 's/^ *[0-9]+ *//')
fi
absdir "$PROC_DIR" || PROC_DIR=""
if [ -z "$PROC_DIR" ]; then
  for cand in "$FW_ROOT/process_files" "$FW_ROOT/processes"; do
    absdir "$cand" && { PROC_DIR="$cand"; break; }
  done
fi

sec "DETECTED-PATHS"
{
  printf 'framework_root\t%s\n'      "$FW_ROOT"
  printf 'schedulers_dir\t%s\n'      "$SCHED_DIR"
  printf 'processes_dir\t%s\n'       "$PROC_DIR"
  printf 'main_sh\t%s\n'             "$MAIN_SH"
  printf 'job_scripts_found\t%s\n'   "$NWRAP"
  printf 'process_refs_resolved\t%s\n' "$(wc -l < "$PROC_REFS" | tr -d ' ')"
} >> "$INV"
end "DETECTED-PATHS"
msg "framework=$FW_ROOT  processes=$PROC_DIR  main.sh=$MAIN_SH"

# ---------------------------------------------------------------------------
# 6. Contratos em disco: indice, censo por cliente, orfaos e referencias quebradas
# ---------------------------------------------------------------------------
if [ -n "$PROC_DIR" ]; then
  $SUDO find "$PROC_DIR" -maxdepth 6 -type f -name '*.json' 2>/dev/null | sort > "$DISK_CONTRACTS"

  sec "CONTRACTS-INDEX | formato=path bytes mtime owner group modo"
  $SUDO find "$PROC_DIR" -maxdepth 6 -type f -name '*.json' \
    -printf '%p\t%s\t%TY-%Tm-%TdT%TH:%TM:%TS\t%u\t%g\t%m\n' >> "$INV" 2>>"$INV"
  end "CONTRACTS-INDEX"

  sec "CLIENT-CENSUS | formato=qtd cliente_pais | fonte=contratos em disco"
  census_client < "$DISK_CONTRACTS" | sort | uniq -c | sed -E 's/^ *([0-9]+) *(.*)/\1\t\2/' >> "$INV" 2>/dev/null
  end "CLIENT-CENSUS"

  sec "CLIENT-DIRS | formato=nome_do_diretorio (um por linha, entre colchetes p/ revelar espacos)"
  $SUDO find "$PROC_DIR" -mindepth 1 -maxdepth 1 -type d -printf '[%f]\n' 2>/dev/null | sort >> "$INV"
  end "CLIENT-DIRS"

  # Patologias de nome: espaco no inicio/fim, tab, byte nao imprimivel
  sec "PATHOLOGY-NAMES | formato=[nome] com espaco no inicio/fim, tab ou byte nao-ASCII"
  $SUDO find "$PROC_DIR" -mindepth 1 -maxdepth 2 -printf '%f\n' 2>/dev/null \
    | LC_ALL=C awk '/^[ \t]/ || /[ \t]$/ || /[^ -~]/ { print "[" $0 "]" }' >> "$INV" 2>/dev/null
  end "PATHOLOGY-NAMES"

  # Colisoes de caixa entre diretorios de cliente
  sec "CASE-COLLISIONS | formato=nome_minusculo repetido"
  $SUDO find "$PROC_DIR" -mindepth 1 -maxdepth 1 -printf '%f\n' 2>/dev/null \
    | tr 'A-Z' 'a-z' | sort | uniq -d >> "$INV" 2>/dev/null
  end "CASE-COLLISIONS"

  # Cruzamento: contrato em disco sem wrapper que o referencie
  sec "ORPHAN-CONTRACTS | formato=path (existe em disco, nenhum wrapper referencia)"
  comm -23 "$DISK_CONTRACTS" <(sort -u "$PROC_REFS") >> "$INV" 2>/dev/null
  end "ORPHAN-CONTRACTS"

  # Cruzamento: wrapper referencia contrato que nao existe
  sec "BROKEN-REFS | formato=path (referenciado por wrapper, ausente em disco)"
  comm -13 "$DISK_CONTRACTS" <(sort -u "$PROC_REFS") >> "$INV" 2>/dev/null
  end "BROKEN-REFS"

  sec "SECRET-SCAN | formato=path linha palavra-chave (sem valores)"
  $SUDO grep -rIno -E '(password|passwd|senha|secret|token|api[_-]?key|private[_-]?key|BEGIN [A-Z ]*PRIVATE KEY)' \
    "$PROC_DIR" >> "$INV" 2>/dev/null
  [ -n "$SCHED_DIR" ] && $SUDO grep -rIno -E '(password|passwd|senha|secret|token|api[_-]?key|private[_-]?key)' \
    "$SCHED_DIR" >> "$INV" 2>/dev/null
  end "SECRET-SCAN"
else
  sec "CONTRACTS-INDEX"
  printf '(diretorio de contratos nao localizado — rode com PROC_DIR=/caminho/absoluto)\n' >> "$INV"
  end "CONTRACTS-INDEX"
  msg "AVISO diretorio de contratos nao localizado — defina PROC_DIR"
fi

if [ -n "$SCHED_DIR" ]; then
  sec "SCHEDULERS-INDEX | formato=path bytes mtime owner group modo"
  $SUDO find "$SCHED_DIR" -maxdepth 4 -type f -name '*.sh' \
    -printf '%p\t%s\t%TY-%Tm-%TdT%TH:%TM:%TS\t%u\t%g\t%m\n' >> "$INV" 2>>"$INV"
  end "SCHEDULERS-INDEX"
fi

# ---------------------------------------------------------------------------
# 7. connections.json — SOMENTE metadado por alias. Nunca valores de senha.
# ---------------------------------------------------------------------------
CONN=${CONNECTIONS_FILE:-}
if [ -z "$CONN" ]; then
  CONN_DIRS="$FW_ROOT"
  [ -n "$MAIN_SH" ] && CONN_DIRS="$CONN_DIRS $(dirname "$MAIN_SH")"
  for d in $CONN_DIRS; do
    [ -n "$d" ] || continue
    [ "$d" = "/" ] && continue
    absdir "$d" || continue
    CONN=$($SUDO find "$d" -maxdepth 3 -type f -name 'connections*.json' 2>/dev/null | head -1)
    [ -n "$CONN" ] && break
  done
fi

: > "$CONNTMP"
sec "CONNECTION-ALIASES | path=$CONN | formato=name type host user port region auth key_path campos"
if [ -n "$CONN" ] && $SUDO test -f "$CONN"; then
  if command -v jq >/dev/null 2>&1; then
    $SUDO cat "$CONN" | jq -r '
      def rows:
        if type=="array" then .[]
        elif type=="object" then (to_entries[] | (.value + {name: ((.value.name) // .key)}))
        else empty end;
      def auth:
        if ((.key // "") | tostring) != "" then "key"
        elif has("password") then "password"
        elif (.type == "S3") then "iam"
        else "none" end;
      rows
      | [ (.name // ""), (.type // ""), (.host // ""), (.user // ""),
          ((.port // "") | tostring), (.region // ""), auth,
          ((.key // "") | tostring), (keys | join(",")) ]
      | @tsv' > "$CONNTMP" 2>/dev/null
  elif command -v python3 >/dev/null 2>&1; then
    $SUDO cat "$CONN" | python3 -c 'import json, sys
d = json.load(sys.stdin)
if isinstance(d, dict):
    items = [dict(v, name=v.get("name", k)) if isinstance(v, dict) else {"name": k} for k, v in d.items()]
elif isinstance(d, list):
    items = [x for x in d if isinstance(x, dict)]
else:
    items = []
for e in items:
    key = str(e.get("key") or "")
    if key:
        auth = "key"
    elif "password" in e:
        auth = "password"
    elif e.get("type") == "S3":
        auth = "iam"
    else:
        auth = "none"
    print(e.get("name", ""), e.get("type", ""), e.get("host", ""), e.get("user", ""),
          e.get("port", ""), e.get("region", ""), auth, key, ",".join(e.keys()), sep="\t")' > "$CONNTMP" 2>/dev/null
  else
    printf '(jq e python3 ausentes — extrair os aliases manualmente)\n' > "$CONNTMP"
  fi
  if [ -s "$CONNTMP" ]; then
    cat "$CONNTMP" >> "$INV"
    msg "aliases de conexao extraidos: $(wc -l < "$CONNTMP" | tr -d ' ')"
  else
    printf '(extracao vazia — formato inesperado de %s)\n' "$CONN" >> "$INV"
    msg "AVISO extracao de connections.json vazia"
  fi
else
  printf '(connections.json nao localizado; defina CONNECTIONS_FILE)\n' >> "$INV"
fi
end "CONNECTION-ALIASES"

sec "CONNECTIONS-STAT"
[ -n "$CONN" ] && $SUDO stat -c '%n %s %U:%G %a %y' "$CONN" >> "$INV" 2>>"$INV"
end "CONNECTIONS-STAT"

# Aliases citados nos contratos, para cruzar com o connections.json
if [ -n "$PROC_DIR" ]; then
  sec "ALIASES-USED-IN-CONTRACTS | formato=qtd alias | fonte=campo connection nos contratos"
  $SUDO grep -rhoE '"connection"[[:space:]]*:[[:space:]]*"[^"]+"' "$PROC_DIR" 2>/dev/null \
    | sed -E 's/.*"([^"]+)"$/\1/' | sort | uniq -c | sed -E 's/^ *([0-9]+) *(.*)/\1\t\2/' >> "$INV" 2>/dev/null
  end "ALIASES-USED-IN-CONTRACTS"
fi

# ---------------------------------------------------------------------------
# 8. Framework: listagem e hashes
# ---------------------------------------------------------------------------
if [ -n "$FW_ROOT" ]; then
  sec "FRAMEWORK-LISTING | root=$FW_ROOT | formato=tipo path bytes mtime owner group modo"
  $SUDO find "$FW_ROOT" -maxdepth 2 \( -type f -o -type d \) \
    -printf '%y\t%p\t%s\t%TY-%Tm-%TdT%TH:%TM:%TS\t%u\t%g\t%m\n' >> "$INV" 2>>"$INV"
  end "FRAMEWORK-LISTING"

  sec "FRAMEWORK-SHA256 | root=$FW_ROOT"
  $SUDO find "$FW_ROOT" -maxdepth 3 -type f \( -name '*.sh' -o -name '*.jar' -o -name '*.py' \) -print0 2>/dev/null \
    | $SUDO xargs -0 -r sha256sum >> "$INV" 2>>"$INV"
  end "FRAMEWORK-SHA256"
fi

if [ "$WITH_LOGS" = "1" ]; then
  sec "LOGS-SAMPLE | formato=path bytes data"
  for d in ${LOGS_DIR:-} "$FW_ROOT/logs" /var/log/batch; do
    absdir "$d" || continue
    printf '### DIR %s total=%s\n' "$d" "$($SUDO find "$d" -maxdepth 3 -type f 2>/dev/null | wc -l | tr -d ' ')" >> "$INV"
    $SUDO find "$d" -maxdepth 3 -type f -printf '%p\t%s\t%TY-%Tm-%Td\n' 2>/dev/null | head -500 >> "$INV"
  done
  end "LOGS-SAMPLE"
fi

# ---------------------------------------------------------------------------
# 9. Copia dos arquivos: contratos + wrappers + engine (arvore preservada)
# ---------------------------------------------------------------------------
NFILES=0
if [ "$WITH_FILES" = "1" ] && [ -n "$FW_ROOT" ]; then
  LIST="$WORK/copy-list"
  : > "$LIST"
  [ -n "$PROC_DIR" ]  && $SUDO find "$PROC_DIR"  -maxdepth 6 -type f -name '*.json' -print0 2>/dev/null >> "$LIST"
  [ -n "$SCHED_DIR" ] && $SUDO find "$SCHED_DIR" -maxdepth 4 -type f -name '*.sh'   -print0 2>/dev/null >> "$LIST"
  if [ "$WITH_ENGINE" = "1" ]; then
    $SUDO find "$FW_ROOT" -maxdepth 1 -type f \( -name '*.sh' -o -name '*.py' \) -print0 2>/dev/null >> "$LIST"
    for sub in lib libs functions include common; do
      absdir "$FW_ROOT/$sub" && $SUDO find "$FW_ROOT/$sub" -maxdepth 2 -type f -name '*.sh' -print0 2>/dev/null >> "$LIST"
    done
  fi

  BYTES=0
  while IFS= read -r -d '' f; do
    case "$f" in *connections*.json) continue ;; esac
    sz=$($SUDO stat -c '%s' "$f" 2>/dev/null); BYTES=$((BYTES + ${sz:-0}))
  done < "$LIST"
  MB=$((BYTES / 1048576))
  msg "arquivos a copiar: $(tr -cd '\0' < "$LIST" | wc -c) (~${MB}MB)"

  if [ "$MB" -gt "$MAX_MB" ]; then
    msg "AVISO copia ABORTADA: ~${MB}MB acima do teto MAX_MB=$MAX_MB. Rode com MAX_MB maior ou WITH_FILES=0."
    printf 'copia abortada por tamanho: ~%sMB > MAX_MB=%s\n' "$MB" "$MAX_MB" > "$STAGE/framework/COPIA-ABORTADA.txt"
  else
    while IFS= read -r -d '' f; do
      case "$f" in
        *connections*.json) continue ;;   # segredo real: nunca copiado
      esac
      rel=${f#"$FW_ROOT"/}
      dest="$STAGE/framework/$rel"
      mkdir -p "$(dirname "$dest")" 2>/dev/null
      $SUDO cat "$f" > "$dest" 2>/dev/null && NFILES=$((NFILES + 1))
    done < "$LIST"
    msg "arquivos copiados: $NFILES"
  fi
fi

# ---------------------------------------------------------------------------
# 10. Fechamento: manifest, checksums, empacotamento
# ---------------------------------------------------------------------------
sec "BUNDLE-END"
{
  printf 'framework_root\t%s\n'    "$FW_ROOT"
  printf 'arquivos_copiados\t%s\n' "$NFILES"
  printf 'contratos_em_disco\t%s\n' "$(wc -l < "$DISK_CONTRACTS" | tr -d ' ')"
  printf 'job_scripts\t%s\n'       "$NWRAP"
  printf 'linhas_cron\t%s\n'       "$TOT"
  printf 'scripts_distintos\t%s\n' "$DISTINCT"
  printf 'finalizado_em_utc\t%s\n' "$(date -u +%Y%m%dT%H%M%SZ)"
} >> "$INV"
end "BUNDLE-END"

cat > "$STAGE/LEIA-ME.txt" <<'LEIAME'
Pacote de seed do job-catalog (Batch DTU) — coleta read-only.

Conteudo:
  inventory.txt   metadados em secoes "##### BEGIN <NOME> | chave=valor" / "##### END <NOME>"
  framework/      contratos JSON, wrappers de scheduler e fonte do engine (arvore relativa
                  a raiz do framework). connections.json NAO esta aqui, por design.
  SHA256SUMS      integridade de tudo acima

No repositorio, na sua maquina:
  tar -xzf batch-seed-<host>-<stamp>.tar.gz -C seed/raw/
  ( cd seed/raw/batch-seed-<host>-<stamp> && sha256sum -c SHA256SUMS --quiet )
  python3 seed/collect/unpack_bundle.py seed/raw/batch-seed-<host>-<stamp>/inventory.txt

Este pacote contem nomes de cliente, IPs internos e caminhos internos.
seed/raw/ esta no .gitignore e deve continuar assim.
LEIAME

( cd "$STAGE" && find . -type f ! -name SHA256SUMS -print0 | xargs -0 -r sha256sum ) > "$STAGE/SHA256SUMS" 2>/dev/null

OUT="$OUT_DIR/$NAME.tar.gz"
tar -czf "$OUT" -C "$WORK" "$NAME" 2>/dev/null || { msg "falha ao empacotar"; exit 1; }
chmod 644 "$OUT" 2>/dev/null
rm -rf "$WORK"

printf '\n' >&2
msg "===================== COLETA CONCLUIDA ====================="
msg "baixe este arquivo ..: $OUT"
msg "tamanho .............: $(du -h "$OUT" 2>/dev/null | cut -f1)"
msg "arquivos empacotados : $NFILES"
msg "sha256 ..............:"
sha256sum "$OUT" >&2 2>/dev/null
printf '\n' >&2
msg "--- caminhos detectados ---"
tar -xzOf "$OUT" "$NAME/inventory.txt" 2>/dev/null | sed -n '/BEGIN DETECTED-PATHS/,/END DETECTED-PATHS/p' >&2
printf '\n' >&2
msg "--- censo de cron (conferir contra 509 / 390 / 119) ---"
tar -xzOf "$OUT" "$NAME/inventory.txt" 2>/dev/null | sed -n '/BEGIN CRON-SUMMARY/,/END CRON-SUMMARY/p' >&2
printf '\n' >&2
msg "Depois do download, apague do servidor:"
msg "  rm -f $OUT /tmp/collect-seed.sh"
