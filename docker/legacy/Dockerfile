# Host legado simulado para desenvolvimento da Fase 1.
#
# Reproduz o que importa do parque real:
#   - conta de serviço `backoffice_svc` separada de `batch_user` (docs/seguranca.md)
#   - authorized_keys restrito por `command=` a um wrapper que revalida server-side
#   - árvore /opt2/batch_v2/batch-commons-framework com main.sh, schedulers/ e processes/
#   - log por execução com `execution_id` no nome do arquivo
#
# Base RHEL-family porque o host real é CentOS 7 ELS; o que muda de verdade é o
# comportamento do sshd com `command=`, não a distro.
FROM almalinux:9-minimal

# `coreutils` NÃO entra na lista: a base `-minimal` já traz `coreutils-single`
# (mesmos utilitários, empacotados como binário multicall) e os dois conflitam —
# pedir `coreutils` aqui quebra o depsolve.
RUN microdnf install -y openssh openssh-server shadow-utils jq findutils gzip \
    && microdnf clean all \
    && rm -rf /var/cache/yum

ENV FW_ROOT=/opt2/batch_v2/batch-commons-framework

RUN groupadd -g 2000 batch \
    && useradd -u 2000 -g batch -m -s /bin/bash batch_user \
    && useradd -u 2001 -g batch -m -s /bin/bash backoffice_svc \
    && mkdir -p "$FW_ROOT"/{schedulers,processes,connections,logs,custom_scripts} \
    && mkdir -p "$FW_ROOT"/schedulers/{base2,emboces,emisiones,otros,reportes,saldos,transacciones} \
    && mkdir -p "$FW_ROOT"/processes/{base2,emboces,emisiones,otros,reportes,saldos,transacciones} \
    && mkdir -p "$FW_ROOT"/logs/schedulers/{base2,emboces,emisiones,otros,reportes,saldos,transacciones}

COPY main.sh $FW_ROOT/main.sh
COPY batch-wrapper.sh /usr/local/bin/batch-wrapper.sh
COPY fixtures/ $FW_ROOT/
COPY entrypoint.sh /usr/local/bin/entrypoint.sh

RUN chmod 0755 "$FW_ROOT/main.sh" /usr/local/bin/batch-wrapper.sh /usr/local/bin/entrypoint.sh \
    && chmod -R 0755 "$FW_ROOT/schedulers" \
    && chown -R batch_user:batch /opt2 \
    && chmod 0600 "$FW_ROOT/connections/connections.json" \
    && chown batch_user:batch "$FW_ROOT/connections/connections.json" \
    && touch /var/log/batch-wrapper.log \
    && chown backoffice_svc:batch /var/log/batch-wrapper.log \
    && chmod 0640 /var/log/batch-wrapper.log

# sshd endurecido como na Fase 1: sem senha, sem forward, só chave.
RUN sed -i \
    -e 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' \
    -e 's/^#\?PermitRootLogin.*/PermitRootLogin no/' \
    -e 's/^#\?AllowTcpForwarding.*/AllowTcpForwarding no/' \
    -e 's/^#\?X11Forwarding.*/X11Forwarding no/' \
    -e 's/^#\?PermitTunnel.*/PermitTunnel no/' \
    /etc/ssh/sshd_config \
    && printf 'AllowUsers backoffice_svc batch_user\nPrintMotd no\n' >> /etc/ssh/sshd_config

VOLUME ["/keys"]
EXPOSE 22

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
