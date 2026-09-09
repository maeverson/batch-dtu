# Módulo: Job Catalog

Contexto ao trabalhar aqui:
- O catálogo é a **fonte da verdade** do parque: alimenta scheduler, RBAC (escopo domínio/cliente) e dashboards. Substitui o crontab nesse papel.
- Modelo de dados em `../../docs/modelo-de-dados.md`; contrato JSON em `../../docs/contrato-json.md`.
- Contratos são versionados; mudanças geram `audit_event`.
