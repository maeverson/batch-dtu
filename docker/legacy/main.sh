#!/bin/bash
#
# main.sh (STUB de desenvolvimento) — imita o engine legado no que a Fase 1
# precisa exercitar, e nada além disso:
#
#   * mesmas flags: --process-file, --manual-steps, --dates-pattern-files,
#     --validate-file, --no-mail
#
# NAO conhece --execution-id, de proposito: o main.sh REAL trata flag
# desconhecida como fatal ("Opcion desconocida" -> main_help -> exit). Um stub
# mais permissivo que o original faria a suite passar aqui e toda execucao
# falhar em UAT. Quem nomeia o log por execution_id e o batch-wrapper.sh.
#   * steps sequenciais com `stop_on_failed` por step
#   * placeholders de data resolvidos contra a data-alvo
#   * log com o `execution_id` no nome do arquivo
#   * multiplas datas serializadas, nunca em paralelo (licao do SOP Zinli)
#
# NAO é o engine real e nao deve virar um: a paridade de verdade é do Executor
# v3, verificada por testes de contrato contra o main.sh de produção.
set -uo pipefail

FW_ROOT=${FW_ROOT:-/opt2/batch_v2/batch-commons-framework}
LOG_ROOT="$FW_ROOT/logs"

PROCESS_FILE=""
MANUAL_STEPS=""
DATES=""
NO_MAIL=0
VALIDATE_ONLY=0
EXECUTION_ID=""

while [ $# -gt 0 ]; do
    case "$1" in
        --process-file)          PROCESS_FILE=${2:-}; shift 2 ;;
        --manual-steps)          MANUAL_STEPS=${2:-}; shift 2 ;;
        --dates-pattern-files)   DATES=${2:-}; shift 2 ;;
        --no-mail)               NO_MAIL=1; shift ;;
        --validate-file)         VALIDATE_ONLY=1; shift ;;
        *) printf 'flag desconhecida: %s\n' "$1" >&2; exit 2 ;;
    esac
done

[ -n "$PROCESS_FILE" ] || { printf 'uso: main.sh --process-file <contrato> [...]\n' >&2; exit 2; }
[ -f "$PROCESS_FILE" ] || { printf 'contrato inexistente: %s\n' "$PROCESS_FILE" >&2; exit 3; }

PROCESS_NAME=$(basename "$PROCESS_FILE" .json)
DOMAIN=$(basename "$(dirname "$PROCESS_FILE")")
EXECUTION_ID="local-$(date -u +%Y%m%d%H%M%S)-$$"

LOG_DIR="$LOG_ROOT/schedulers/$DOMAIN"
mkdir -p "$LOG_DIR" 2>/dev/null
# `execution_id` no NOME do arquivo é o que a Fase 1 exige para correlacionar
# auditoria e log (docs/api/platform-api.md).
LOG_FILE="$LOG_DIR/${PROCESS_NAME}.${EXECUTION_ID}.log"

emit() {
    printf '%s execution_id=%s process=%s %s\n' \
        "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$EXECUTION_ID" "$PROCESS_NAME" "$*" | tee -a "$LOG_FILE"
}

# --- validação de contrato (equivalente a --validate-file) -------------------
if ! jq empty "$PROCESS_FILE" 2>/dev/null; then
    emit "level=error msg=\"contrato nao e JSON valido\""
    exit 4
fi

STEP_COUNT=$(jq '(.steps // []) | length' "$PROCESS_FILE")
if [ "$STEP_COUNT" = "0" ]; then
    emit "level=error msg=\"contrato sem steps\""
    exit 5
fi

if [ "$VALIDATE_ONLY" = 1 ]; then
    emit "level=info msg=\"validacao ok\" steps=$STEP_COUNT schema_version=$(jq -r '.schema_version // "ausente"' "$PROCESS_FILE")"
    exit 0
fi

# --- resolução da data-alvo --------------------------------------------------
resolve_placeholders() {
    local text=$1 target=$2
    local yyyy mm dd juliano
    yyyy=${target:0:4}; mm=${target:4:2}; dd=${target:6:2}
    juliano=$(date -u -d "$yyyy-$mm-$dd" +%j 2>/dev/null || echo 000)
    text=${text//@@@YYYYMMDD@@@/$target}
    text=${text//@@@YYYY@@@/$yyyy}
    text=${text//@@@MM@@@/$mm}
    text=${text//@@@DD@@@/$dd}
    text=${text//@@@JULIANO@@@/$juliano}
    printf '%s' "$text"
}

if [ -z "$DATES" ]; then
    DATES=$(date -u +%Y%m%d)
fi

STATUS=0
# Serialização explícita: paralelizar por processo causa timeout e contenção.
IFS=',' read -r -a DATE_LIST <<< "$DATES"
for TARGET in "${DATE_LIST[@]}"; do
    TARGET=${TARGET//-/}
    emit "level=info msg=\"inicio\" target_date=$TARGET steps=$STEP_COUNT manual_steps=${MANUAL_STEPS:-todos} no_mail=$NO_MAIL"

    # --manual-steps aceita lista e faixa (3,4 e 3-5); o wrapper valida o
    # formato, aqui a faixa é expandida para casar com a semântica do legado.
    SELECTED=""
    if [ -n "$MANUAL_STEPS" ]; then
        IFS=',' read -r -a parts <<< "$MANUAL_STEPS"
        for part in "${parts[@]}"; do
            case "$part" in
                *-*) SELECTED="$SELECTED,$(seq -s, "${part%%-*}" "${part##*-}")" ;;
                *)   SELECTED="$SELECTED,$part" ;;
            esac
        done
        SELECTED="$SELECTED,"
    fi

    for index in $(seq 1 "$STEP_COUNT"); do
        if [ -n "$SELECTED" ] && ! printf '%s' "$SELECTED" | grep -q ",$index,"; then
            emit "level=debug msg=\"step ignorado (fora de --manual-steps)\" step=$index"
            continue
        fi

        step_json=$(jq -c ".steps[$((index - 1))]" "$PROCESS_FILE")
        step_type=$(printf '%s' "$step_json" | jq -r '.type // .funcion // "desconhecido"')
        stop_on_failed=$(printf '%s' "$step_json" | jq -r '.stop_on_failed // false')
        alias_name=$(printf '%s' "$step_json" | jq -r '.connection // "-"')
        raw_target=$(printf '%s' "$step_json" | jq -r '.file // .path // "-"')
        resolved=$(resolve_placeholders "$raw_target" "$TARGET")
        simulate_failure=$(printf '%s' "$step_json" | jq -r '.dev_fail // false')

        emit "level=info msg=\"step\" step=$index type=$step_type connection=$alias_name target=$resolved"

        if [ "$simulate_failure" = "true" ]; then
            emit "level=error msg=\"step falhou (dev_fail)\" step=$index stop_on_failed=$stop_on_failed"
            STATUS=1
            if [ "$stop_on_failed" = "true" ]; then
                emit "level=error msg=\"interrompido por stop_on_failed\" step=$index target_date=$TARGET"
                break
            fi
        fi
    done

    emit "level=info msg=\"fim\" target_date=$TARGET status=$STATUS"
done

if [ "$NO_MAIL" = 0 ]; then
    emit "level=info msg=\"notificacao enviada\" transport=smtp"
fi

emit "level=info msg=\"encerrado\" status=$STATUS log=$LOG_FILE"
exit "$STATUS"
