# Back Office — Guia de operação

Referência de uso do app `frontend/` (React + Vite + TypeScript). `CLAUDE.md` dá o contexto e as
decisões; `SPEC.md` dá requisitos e critérios de aceite. Este documento é "como rodar e testar",
na linha de `modules/platform-api/OPERACAO.md`.

## Stack

Vite + React 19 + TypeScript, roteamento com `react-router-dom`, autenticação OIDC (PKCE, sem
client secret — é uma SPA) com `react-oidc-context`/`oidc-client-ts`. Sem UI kit pesado: CSS
próprio em `src/index.css`. Sem `react-query`/Redux: `src/api/hooks.ts` tem um `useQuery` bem
pequeno, com polling opcional — suficiente para o volume de dados da Fase 1.

## Subir o ambiente

```bash
docker compose --profile auth up -d          # Keycloak (OIDC local) — cliente "back-office" já no realm
docker compose up -d postgres loki grafana   # dependências da Platform API
export DATABASE_URL=postgresql+psycopg://batch_app:batch_app_dev@localhost:5432/batch_catalog
platform-api                                 # :8000 — ver modules/platform-api/OPERACAO.md

cd frontend
cp .env.example .env.local                   # defaults já apontam pro ambiente local
npm install
npm run dev                                  # :5173
```

Abra `http://localhost:5173`. Login redireciona para o Keycloak local; usuários de teste
`viewer/operator/operator-prod/admin-batch` (senha = usuário) — os mesmos do
`modules/platform-api/OPERACAO.md`. **Sem uma linha em `role_binding` para o subject**, o login
funciona mas o catálogo aparece vazio (mesma regra do backend — ver `CLAUDE.md`).

## Por que um cliente OIDC separado do `platform-api`

O realm (`docker/keycloak/realm-batch-dtu.json`) tem dois clientes: `platform-api` (confidencial,
usado por teste automatizado e pela própria API para validar token) e `back-office` (**público**,
PKCE, sem secret — nunca embarcar segredo em SPA). Os dois precisam emitir token com
`aud: platform-api`, porque é essa audience que `TokenVerifier` (`src/platform_api/security.py`)
exige — por isso o cliente `back-office` tem o mesmo `protocolMapper` de audiência que o
`platform-api` já tinha. Roles (`realm_access.roles`) já vêm de graça do scope padrão `roles`.

## CORS

A Platform API só libera `http://localhost:5173`/`:3001` por padrão
(`CORS_ALLOW_ORIGINS` em `src/platform_api/config.py`) — são as mesmas origens registradas no
cliente `back-office` do Keycloak. Subir o front em outra porta exige ajustar as duas pontas.

## O que "tempo real" quer dizer na Fase 1

`POST /executions` é **síncrono** — só responde quando o SSH termina (`src/platform_api/routers/
executions.py`). Na prática, pela hora em que a resposta chega no navegador, a execução já está em
estado terminal; não existe um "acompanhar rodando" de verdade ainda (isso é trabalho de Fase 2,
quando o backend vira assíncrono via orchestrator). O que a página de execução
(`src/pages/ExecutionDetailPage.tsx`) faz hoje, honestamente:

- Reconsulta `GET /executions/{id}` a cada poucos segundos — útil se OUTRA aba/usuário está
  olhando antes da chamada síncrona original terminar.
- Reconsulta `GET /executions/{id}/logs` (proxy Loki) no mesmo ritmo — o promtail
  (`docker/promtail/`, observability Etapa 1.3) tem um atraso de alguns segundos para embarcar o
  `.log`, então linhas continuam chegando um pouco depois da execução já ter terminado. É esse
  atraso, não streaming de verdade, que faz valer a pena continuar reconsultando.

## Verificar

```bash
cd frontend
npm run build     # tsc -b (type-check) + vite build — pega erro de tipo/import quebrado
npx oxlint src    # lint
npm run dev       # sobe o servidor de dev em :5173
```

**Não há teste automatizado de UI neste módulo ainda** — não foi possível instalar um browser
headless neste ambiente de desenvolvimento (Playwright recusa a instalar Chromium por não
reconhecer a distro do sandbox; mesma classe de limitação de rede/ambiente já registrada para o
container `docker/legacy` em `modules/platform-api/OPERACAO.md`). O que foi verificado aqui:
`tsc -b` (sem erro de tipo), `vite build` (bundle gera sem quebrar import), `oxlint` limpo, e o
servidor de dev sobe e serve `index.html`/`main.tsx` sem erro de servidor. **Falta**: um teste
end-to-end de verdade (login PKCE completo, clicar em executar, ver o resultado) num navegador
real — fazer isso manualmente antes do marco de reprocessamento Zinli/MFTech fim-a-fim
(`ROADMAP.md`, 🏁 Fase 1).

## Endpoints da Platform API usados

Só os documentados em `docs/api/platform-api.md` — `GET /me` (identidade/escopo, novo nesta
etapa), `GET /jobs`, `GET /jobs/{id}`, `GET /jobs/{id}/schedules`, `GET /jobs/{id}/contract`,
`GET /jobs/{id}/reconciliation` (os três novos nesta etapa), `POST /jobs/{id}/validate`,
`PATCH /jobs/{id}/status`, `POST /executions`, `GET /executions`, `GET /executions/{id}`,
`GET /executions/{id}/logs`, `GET /change-requests`, `POST /change-requests/{id}/cancel`.
