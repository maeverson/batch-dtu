# Módulo: Platform API

Contexto ao trabalhar aqui:
- Leia `../../docs/api/platform-api.md` (contrato REST) e `../../docs/contrato-json.md` antes de qualquer implementação.
- **O contrato REST não muda entre fases** — só o backend de execução (Fase 1: SSH parametrizado; Fase 2: enfileiramento no orchestrator). Isole o backend atrás de uma interface (`ExecutionBackend`) desde o dia 1.
- **Nunca interpole shell arbitrário.** Linha de comando montada exclusivamente de campos tipados validados contra o catálogo.
- Toda ação de escrita gera `audit_event` (append-only) e toda execução gera registro `execution` com `execution_id` propagado aos logs.
- Autenticação: Entra ID (OIDC). Autorização: roles `batch.*` com escopo domínio/ambiente (ver `../../docs/seguranca.md`).
