# Módulo: Back Office (React)

Contexto ao trabalhar aqui:
- Frontend React; **única interface operacional** no estado-alvo. Nunca acessa o plano de execução diretamente — tudo via Platform API.
- Consuma o contrato de `../../docs/api/platform-api.md`. A UI não deve conhecer o mecanismo de execução (SSH legado ou orchestrator).
- Fluxos sensíveis (reprocesso com `upload_remote`) exigem **workflow de confirmação com dupla checagem de data-alvo** — risco documentado de reenvio errôneo a cliente (SOP Zinli).
- Renderização condicionada a role/escopo do usuário (Entra ID): viewer, operator, operator-prod, admin.
