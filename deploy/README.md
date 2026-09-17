# `deploy/` — configuração por serviço

Regra desta pasta, e do projeto: **as variáveis sobem junto com o serviço**.
Nada de `export` no `.bashrc` do host, nada de `/etc/environment`, nada de
"lembrar de exportar antes de subir". Cada módulo tem o seu arquivo de
ambiente, que vira:

- `env_file:` do container (compose) ou `envFrom:` de um `Secret`/`ConfigMap` (EKS);
- `EnvironmentFile=` da unit, se algum dia rodar como systemd;
- `set -a; . <arquivo>; set +a` no desenvolvimento local, escopado ao shell daquela janela.

Consequência prática: **quem lê a configuração é o processo, não a sessão**.
Trocar UAT por PROD é trocar o arquivo montado, não reexportar variável nenhuma.

```
deploy/
├── platform-api/     uat.env.example · prod.env.example · local.env
├── back-office/      uat.env.example · prod.env.example   (Vite: build time!)
├── catalog/          ingest.env.example                   (job de carga)
└── newrelic/         newrelic.ini · logging.d/            (APM + logs do host)
```

## O que NUNCA entra nestes arquivos

Segredo (invariante 5). Os `*.example` trazem a **chave**, nunca o **valor**:

| Variável | De onde o valor vem em produção |
|---|---|
| `DATABASE_URL` | Secrets Manager / vault, injetado como env do pod |
| `SSH_BACKEND_KEY` | caminho de um arquivo montado do vault (`0600`), não a chave |
| `NEW_RELIC_LICENSE_KEY` | Secrets Manager (ingestão) |
| `NEW_RELIC_API_KEY` | Secrets Manager (User Key, só consulta) |

`local.env` é a única exceção, e só porque aponta exclusivamente para o
`docker-compose.yaml` deste repositório: Keycloak local, host legado simulado,
Postgres de desenvolvimento. Nenhum valor ali existe fora da sua máquina.

## Um deploy por ambiente

`PLATFORM_ENVIRONMENT` + `PLATFORM_HOST` definem **o que a instância serve**;
`SSH_BACKEND_*` define **onde ela executa**. Os quatro andam juntos: uma
instância de UAT não lista, não executa e não administra job de PROD — nem
para `batch.admin`. Trocar um sem o outro é como a plataforma descobre que
alguém apontou UAT para o host errado, e é por isso que `/health` devolve os
dois.

## Ordem de aplicação

1. `platform-api/<ambiente>.env` → sobe a API, `/health` responde com o ambiente certo.
2. `back-office/<ambiente>.env` → **build** do SPA (Vite embute em build time;
   trocar depois não tem efeito — é preciso rebuildar).
3. `catalog/ingest.env` → job de carga do catálogo, quando houver.
4. `newrelic/` → agente no host do `main.sh` (logs) e APM no pod da API.
