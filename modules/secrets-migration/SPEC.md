# SPEC — Migração de Segredos

## Requisitos

1. **Migrador alias-a-alias** com verificação (comparação vault × arquivo por alias).
2. **Dual-read** durante a transição + checagem de consistência agendada; divergência gera alerta.
3. **Metadados** em `connection_alias` (name, type, vault_reference, owners, validity) — segredo real só no vault.
4. **Ciclo de vida de chaves GPG de cliente**: validade monitorada, alerta proativo de expiração (hoje só descoberta na falha do `encrypt`).
5. **Rotação automática** onde suportado; tokens de observabilidade (`glc_...`) migram de unit files systemd para parameter store/vault.

## Critérios de aceite

- [ ] 100% dos aliases migrados com verificação de consistência.
- [ ] Alerta de expiração GPG disparando com antecedência configurável.
- [ ] `connections.json` congelado e removido do caminho de leitura após a última wave.
