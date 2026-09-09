# Módulo: Migração de Segredos

Contexto ao trabalhar aqui:
- `connections.json` → secrets manager (candidato: AWS Secrets Manager), **alias por alias, 1:1**, preservando o modelo mental operacional do time.
- Contratos não mudam: continuam referenciando aliases; muda a implementação do dicionário.
- Janela de **dual-read**: Executor v3 lê do vault; legado continua lendo do arquivo, com checagens de consistência automatizadas (evolução do `validate_connections_json.sh`).
