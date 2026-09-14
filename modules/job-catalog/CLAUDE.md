# Módulo: Job Catalog

Contexto ao trabalhar aqui:
- O catálogo é a **fonte da verdade** do parque: alimenta scheduler, RBAC (escopo domínio/cliente) e dashboards. Substitui o crontab nesse papel.
- Modelo de dados em `../../docs/modelo-de-dados.md`; contrato JSON em `../../docs/contrato-json.md`.
- Contratos são versionados; mudanças geram `audit_event`.
- **Operação do CLI**: `OPERACAO.md` neste diretório — os 9 comandos de `catalog` (import, load,
  reconcile, triage, explain, validate, sample, history, check-names), na ordem em que se encaixam,
  e qual comando fecha cada critério de aceite do `SPEC.md`.
