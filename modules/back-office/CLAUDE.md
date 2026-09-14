# Módulo: Back Office (React)

## Decisões tomadas (Etapa 1.4)

| Tema | Decisão |
|---|---|
| Código | `frontend/` na raiz do repo (não `modules/back-office/`, que fica só com docs — mesmo padrão do `platform-api`, cujo código mora em `src/platform_api/`) |
| Stack | Vite + React 19 + TypeScript. Sem UI kit pesado (CSS próprio, `src/index.css`); sem Redux/react-query (`src/api/hooks.ts` tem um `useQuery` pequeno com polling opcional) |
| Auth | OIDC PKCE via `react-oidc-context`/`oidc-client-ts`, cliente **público** `back-office` no realm Keycloak (`docker/keycloak/realm-batch-dtu.json`) — nunca client secret numa SPA |
| Estado do usuário | `GET /me` (novo endpoint da Platform API) devolve subject/roles/escopo visível — a UI usa isso só para decidir o que OFERECER (`src/rbac.ts`); quem autoriza de verdade continua sendo o 403 do servidor |

Contexto ao trabalhar aqui:
- Frontend React; **única interface operacional** no estado-alvo. Nunca acessa o plano de execução diretamente — tudo via Platform API.
- Consuma o contrato de `../../docs/api/platform-api.md`. A UI não deve conhecer o mecanismo de execução (SSH legado ou orchestrator).
- Fluxos sensíveis (reprocesso com `upload_remote`) exigem **workflow de confirmação com dupla checagem de data-alvo** — risco documentado de reenvio errôneo a cliente (SOP Zinli). Ver `frontend/src/components/ExecuteModal.tsx`.
- Renderização condicionada a role/escopo do usuário (Entra ID): viewer, operator, operator-prod, admin. Ver `frontend/src/rbac.ts`.
- **`POST /executions` é síncrono na Fase 1** — a UI não tem um "acompanhar rodando" de verdade
  ainda (isso é Fase 2, orchestrator assíncrono). Ver a nota em `OPERACAO.md` ("O que 'tempo real'
  quer dizer na Fase 1") antes de prometer mais do que o backend atual entrega.
- **Operação do serviço**: `OPERACAO.md` neste diretório — como subir, autenticar, verificar (e a
  limitação conhecida: sem browser headless disponível neste ambiente de desenvolvimento para
  teste end-to-end de UI).
