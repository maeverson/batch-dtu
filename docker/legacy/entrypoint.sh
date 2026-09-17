#!/bin/bash
#
# Prepara o host legado simulado e sobe o sshd.
#
# A chave do `backoffice_svc` é gerada no primeiro start dentro de /keys (bind
# mount), para a Platform API usar no desenvolvimento. O authorized_keys nasce
# restrito por `command=`, como exige docs/seguranca.md — não existe caminho de
# sessão interativa para essa conta.
set -euo pipefail

FW_ROOT=${FW_ROOT:-/opt2/batch_v2/batch-commons-framework}
KEY_DIR=/keys
KEY_FILE="$KEY_DIR/backoffice_svc_ed25519"

log() { printf '[legacy] %s\n' "$*" >&2; }

mkdir -p "$KEY_DIR"

# Chaves de host (persistem no /keys para o known_hosts do cliente não mudar)
for type in rsa ecdsa ed25519; do
    host_key="$KEY_DIR/ssh_host_${type}_key"
    if [ ! -f "$host_key" ]; then
        ssh-keygen -q -t "$type" -f "$host_key" -N "" -C "batch-legacy-host"
    fi
    install -m 0600 "$host_key" "/etc/ssh/ssh_host_${type}_key"
    install -m 0644 "$host_key.pub" "/etc/ssh/ssh_host_${type}_key.pub"
done

# Chave da conta de serviço
if [ ! -f "$KEY_FILE" ]; then
    log "gerando chave do backoffice_svc em $KEY_FILE"
    ssh-keygen -q -t ed25519 -f "$KEY_FILE" -N "" -C "backoffice_svc@batch-legacy"
fi
chmod 0600 "$KEY_FILE"
chmod 0644 "$KEY_FILE.pub"

# `/keys` é bind mount: quem consome esta chave é a Platform API rodando NO
# HOST, não algo aqui dentro. Sem este chown ela nasce root:root 0600 e o
# usuário do host não consegue lê-la — nem pela API (asyncssh: Permission
# denied) nem pelo `ssh -i` que este script sugere no fim. Mantém 0600: muda o
# dono, não a exposição (o `ssh` recusa chave privada legível por outros).
chown "${KEYS_UID:-1000}:${KEYS_GID:-1000}" "$KEY_FILE" "$KEY_FILE.pub"

install -d -m 0700 -o backoffice_svc -g batch /home/backoffice_svc/.ssh
{
    printf 'command="/usr/local/bin/batch-wrapper.sh",restrict '
    cat "$KEY_FILE.pub"
} > /home/backoffice_svc/.ssh/authorized_keys
chown backoffice_svc:batch /home/backoffice_svc/.ssh/authorized_keys
chmod 0600 /home/backoffice_svc/.ssh/authorized_keys

# batch_user recebe a mesma chave, mas SEM `command=`: é o contraste que
# permite testar que o caminho irrestrito existe e é justamente o que a
# plataforma NÃO deve usar (break-glass).
install -d -m 0700 -o batch_user -g batch /home/batch_user/.ssh
cp "$KEY_FILE.pub" /home/batch_user/.ssh/authorized_keys
chown batch_user:batch /home/batch_user/.ssh/authorized_keys
chmod 0600 /home/batch_user/.ssh/authorized_keys

# O parque tem DOIS escritores nesta árvore: `batch_user` (main.sh disparado
# pelo cron, em logs/schedulers/) e `backoffice_svc` (o wrapper da plataforma,
# em logs/backoffice/ — o que o promtail tailha). Ambos estão no grupo `batch`,
# então é o grupo que precisa escrever; setgid para diretório novo nascer no
# grupo certo. Sem isto o `tee` do wrapper falha e a correlação por
# `execution_id` no Loki nunca recebe linha nenhuma.
chown -R batch_user:batch "$FW_ROOT/logs"
find "$FW_ROOT/logs" -type d -exec chmod 2775 {} +
find "$FW_ROOT/logs" -type f -exec chmod 0664 {} + 2>/dev/null || true

log "framework em $FW_ROOT"
log "contratos: $(find "$FW_ROOT/processes" -name '*.json' | wc -l) | wrappers: $(find "$FW_ROOT/schedulers" -name '*.sh' | wc -l)"
log "chave para a Platform API: docker/legacy/keys/backoffice_svc_ed25519"
log "teste: ssh -i docker/legacy/keys/backoffice_svc_ed25519 -p 2222 backoffice_svc@localhost \\"
log "         '$FW_ROOT/main.sh --process-file $FW_ROOT/processes/reportes/prd_aaa_col_rpt.json --validate-file'"

exec /usr/sbin/sshd -D -e
