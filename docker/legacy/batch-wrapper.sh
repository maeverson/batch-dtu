#!/bin/bash
#
# batch-wrapper.sh — alvo do `command=` no authorized_keys do backoffice_svc.
#
# É o controle de segurança da Fase 1 (docs/seguranca.md, docs/api/platform-api.md):
# a Platform API monta a linha de comando a partir de campos tipados e ESTE
# script revalida tudo do lado do servidor. Nada aqui interpola shell: a
# invocação do main.sh é feita por argv, sem `eval`, sem subshell.
#
# Aceita exclusivamente:
#   main.sh --process-file <contrato sob $FW_ROOT/processes> \
#           [--manual-steps <lista>] [--dates-pattern-files <datas>] \
#           [--no-mail] [--validate-file] [--execution-id <uuid>]
#
# Qualquer outra coisa é recusada com exit 42 e registrada.
set -uo pipefail

FW_ROOT=${FW_ROOT:-/opt2/batch_v2/batch-commons-framework}
MAIN_SH="$FW_ROOT/main.sh"
PROC_DIR="$FW_ROOT/processes"
AUDIT_LOG=/var/log/batch-wrapper.log

log() {
    printf '%s\tuser=%s\tfrom=%s\tdecision=%s\tdetail=%s\tcommand=%s\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(id -un)" "${SSH_CLIENT%% *}" \
        "$1" "$2" "${SSH_ORIGINAL_COMMAND:-<vazio>}" >> "$AUDIT_LOG" 2>/dev/null
}

deny() {
    log DENY "$1"
    printf 'RECUSADO pelo wrapper: %s\n' "$1" >&2
    exit 42
}

CMD=${SSH_ORIGINAL_COMMAND:-}
[ -n "$CMD" ] && [ "$CMD" != "" ] || deny "comando vazio (sessao interativa nao e permitida)"

# 1. Nenhum metacaractere de shell. Se a API montou a linha a partir de campos
#    tipados, nada disso pode aparecer — a presença já indica tentativa de escape.
case "$CMD" in
    *';'*|*'|'*|*'&'*|*'$'*|*'`'*|*'>'*|*'<'*|*'('*|*')'*|*'{'*|*'}'*|*$'\n'*|*$'\r'*|*'*'*|*'?'*|*'~'*|*'\\'*)
        deny "metacaractere de shell no comando"
        ;;
esac

# 2. Tokenização sem shell: só divisão por espaço, já que o passo 1 garantiu
#    que não há quoting nem expansão a resolver.
read -r -a ARGV <<< "$CMD"
[ "${#ARGV[@]}" -ge 3 ] || deny "comando curto demais para ser uma invocacao valida"

# 3. O executável tem de ser exatamente o main.sh do framework.
case "${ARGV[0]}" in
    "$MAIN_SH"|main.sh|./main.sh) : ;;
    *) deny "executavel nao permitido: ${ARGV[0]}" ;;
esac

PROCESS_FILE=""
MANUAL_STEPS=""
DATES=""
NO_MAIL=0
VALIDATE=0
EXECUTION_ID=""

index=1
while [ "$index" -lt "${#ARGV[@]}" ]; do
    flag=${ARGV[$index]}
    next=${ARGV[$((index + 1))]:-}
    case "$flag" in
        --process-file)
            [ -n "$next" ] || deny "--process-file sem valor"
            PROCESS_FILE=$next
            index=$((index + 2))
            ;;
        --manual-steps)
            [ -n "$next" ] || deny "--manual-steps sem valor"
            [[ $next =~ ^[0-9]+([,-][0-9]+)*$ ]] || deny "--manual-steps fora do formato: $next"
            MANUAL_STEPS=$next
            index=$((index + 2))
            ;;
        --dates-pattern-files)
            [ -n "$next" ] || deny "--dates-pattern-files sem valor"
            [[ $next =~ ^[0-9]{8}([,][0-9]{8})*$|^[0-9]{4}-[0-9]{2}-[0-9]{2}([,][0-9]{4}-[0-9]{2}-[0-9]{2})*$ ]] \
                || deny "--dates-pattern-files fora do formato: $next"
            DATES=$next
            index=$((index + 2))
            ;;
        --execution-id)
            [ -n "$next" ] || deny "--execution-id sem valor"
            [[ $next =~ ^[0-9a-fA-F-]{8,64}$ ]] || deny "--execution-id fora do formato"
            EXECUTION_ID=$next
            index=$((index + 2))
            ;;
        --no-mail)      NO_MAIL=1; index=$((index + 1)) ;;
        --validate-file) VALIDATE=1; index=$((index + 1)) ;;
        *) deny "flag nao permitida: $flag" ;;
    esac
done

# 4. O contrato tem de estar sob processes/, sem travessia, e existir.
[ -n "$PROCESS_FILE" ] || deny "--process-file e obrigatorio"
case "$PROCESS_FILE" in
    "$PROC_DIR"/*) : ;;
    *) deny "contrato fora de $PROC_DIR: $PROCESS_FILE" ;;
esac
case "$PROCESS_FILE" in
    *..*) deny "travessia de diretorio no caminho do contrato" ;;
esac
[ -f "$PROCESS_FILE" ] || deny "contrato inexistente: $PROCESS_FILE"

# 5. Execução por argv. Sem eval, sem shell intermediário.
ARGS=(--process-file "$PROCESS_FILE")
[ -n "$MANUAL_STEPS" ] && ARGS+=(--manual-steps "$MANUAL_STEPS")
[ -n "$DATES" ] && ARGS+=(--dates-pattern-files "$DATES")
[ "$NO_MAIL" = 1 ] && ARGS+=(--no-mail)
[ "$VALIDATE" = 1 ] && ARGS+=(--validate-file)

# 6. `execution_id` no nome do log SEM tocar no legado.
#
#    O main.sh real nao aceita identificador de execucao (flag desconhecida e
#    fatal) e calcula o proprio caminho de log a partir da data. Quem controla o
#    redirecionamento e este wrapper, entao e aqui que a correlacao nasce: o
#    log da plataforma leva o execution_id no nome, e a linha ALLOW abaixo liga
#    os dois para quem for auditar.
BO_LOG=""
if [ -n "$EXECUTION_ID" ]; then
    PROC_NAME=$(basename "$PROCESS_FILE" .json)
    DOMAIN=$(basename "$(dirname "$PROCESS_FILE")")
    BO_LOG_DIR="${BO_LOG_DIR:-$FW_ROOT/logs/backoffice/$DOMAIN}"
    mkdir -p "$BO_LOG_DIR" 2>/dev/null
    BO_LOG="$BO_LOG_DIR/${PROC_NAME}.${EXECUTION_ID}.log"
fi

log ALLOW "process_file=$PROCESS_FILE steps=${MANUAL_STEPS:-todos} dates=${DATES:-hoje} execution_id=${EXECUTION_ID:-nenhum} bo_log=${BO_LOG:-nenhum}"

if [ -n "$BO_LOG" ]; then
    # `tee` preserva o stream para o SSH (a API acompanha o andamento) e grava
    # o arquivo correlacionado. PIPESTATUS devolve o codigo do main.sh, nao o
    # do tee — o resultado do job nao pode ser mascarado.
    "$MAIN_SH" "${ARGS[@]}" 2>&1 | tee -a "$BO_LOG"
    exit "${PIPESTATUS[0]}"
fi
exec "$MAIN_SH" "${ARGS[@]}"
