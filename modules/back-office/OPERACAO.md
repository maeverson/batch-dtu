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

## Entra ID — o escopo do login (o erro caro)

No Keycloak local o escopo default (`openid profile email`) basta, porque a audiência
`platform-api` vem de um protocol mapper do realm. **No Entra ID não basta**: sem pedir
`api://<client-id-da-api>/access_as_user`, o access token sai com audiência do Microsoft Graph e a
Platform API recusa TODA chamada com 401 — com o login funcionando normalmente, que é o que torna
esse erro caro de diagnosticar. Por isso o escopo é variável (`VITE_OIDC_SCOPE`, em
`src/config.ts`), e `deploy/back-office/*.env.example` já traz a forma certa.

Vite embute as variáveis em **build time**: trocar o arquivo depois do build não tem efeito.

## Administração (`batch.admin`)

`src/pages/AdminPage.tsx` — CRUD de catálogo contra `/admin/*`: criar job, editar metadados,
desativar e publicar nova versão de contrato. A aba só aparece para `batch.admin` (`GET /me`), e a
página repete o gate porque a URL pode ser digitada à mão. Ambiente e host não são campos: vêm da
instância. Motivo é obrigatório em toda escrita.

**Desativar não para o cron** — a tela diz isso no próprio prompt de confirmação, porque é a
confusão que geraria incidente: o job segue rodando no host até a mudança de agenda ser aplicada.

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

`POST /executions` **responde 200 assim que despacha** (`status: running`), sem esperar o
`main.sh`. Então a página de execução (`src/pages/ExecutionDetailPage.tsx`) agora está
acompanhando uma execução que de fato ainda está rodando — o polling deixou de ser um consolo e
virou o mecanismo:

- Reconsulta `GET /executions/{id}` a cada poucos segundos até o estado terminal.
- Reconsulta `GET /executions/{id}/logs` no mesmo ritmo — o agente de log (New Relic em ambiente
  implantado, promtail no compose local) tem alguns segundos de atraso para embarcar o `.log`,
  então linhas continuam chegando depois do desfecho.

Continua não sendo stream de verdade (isso é Fase 2, com o orchestrator). E execução que **trava**
não aparece aqui como erro: fica `running` para sempre. Quem avisa é o alerta do New Relic
(`deploy/newrelic/README.md`), não esta tela.

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
