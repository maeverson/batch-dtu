# SPEC — Back Office

## Objetivo

Interface web para navegar o catálogo, disparar execuções manuais e reprocessos, monitorar em tempo real e gerir habilitação de jobs com motivos registrados (substitui os comentários do crontab).

## Funcionalidades

1. **Navegação do catálogo** — lista/busca/filtros (domínio, cliente, país, ambiente, status, criticidade); detalhe do job com contrato JSON, agenda, owner, SLA, histórico de execuções.
   **Implementado**: `CatalogPage.tsx` (filtros domínio/cliente/ambiente/status — país e criticidade
   ainda não têm filtro dedicado, só exibição) + `JobDetailPage.tsx` (contrato JSON, agenda, owner,
   SLA e histórico). Contrato/agenda/reconciliação exigiram 3 endpoints novos na Platform API
   (`GET /jobs/{id}/{schedules,contract,reconciliation}` — ver `docs/api/platform-api.md`).
2. **Execução manual** — seleção de steps (`--manual-steps`), data(s)-alvo, opção no-mail; pré-validação; **confirmação explícita de data-alvo**; para steps `upload_remote` em PROD, confirmação reforçada (usuário redigita a data).
   **Implementado**: `components/ExecuteModal.tsx` — fluxo em 3 passos (formulário → confirmar
   data-alvo → redigitar data se PROD+upload_remote). Pré-validação continua sendo a que a API já
   roda antes de despachar (`docs/api/platform-api.md`), a UI não duplica.
3. **Reprocesso multi-data** — intervalo de datas com preview da serialização; indicador de que execuções do mesmo processo não paralelizam.
   **Implementado**: mesmo `ExecuteModal.tsx` — lista de datas com preview da ordem de serialização
   e aviso de que uma segunda tentativa concorrente recebe 409.
4. **Monitoramento em tempo real** — status da execução, stream de logs (Loki por `execution_id`), resultado por step.
   **Implementado com ressalva**: `ExecutionsPage.tsx` (lista com auto-refresh) e
   `ExecutionDetailPage.tsx` (status + logs via proxy Loki, ambos por polling). "Resultado por
   step" **não** está disponível — nem a API expõe isso hoje (`execution.result` é agregado:
   `success`/`failure`/`partial`, sem granularidade por step; isso nasce estruturado só no
   Executor v3, Fase 2). E "tempo real" na Fase 1 é reconsulta, não stream de verdade, porque
   `POST /executions` é síncrono — ver `OPERACAO.md`.
5. **Habilitar/desabilitar** — com `reason` obrigatório; Fase 1 mostra estado de reconciliação (diff crontab × catálogo).
   **Implementado**: `components/StatusChangeModal.tsx` + painel de reconciliação em
   `JobDetailPage.tsx` (via `GET /jobs/{id}/reconciliation`, endpoint novo).
6. **Gestão de janelas** — visualização de janelas/agendas por job.
   **Implementado**: seção "Agenda" em `JobDetailPage.tsx`.
7. **Fase 3** — fila de aprovações (two-person rule), wizard de onboarding no-code de jobs (arquétipos: fechamento, MFT, relatórios) com promoção UAT → PROD.

## RBAC na UI

| Role | UI |
|---|---|
| `batch.viewer` | somente leitura |
| `batch.operator` | executar/reprocessar em UAT/TEST |
| `batch.operator-prod` | + PROD com confirmação reforçada |
| `batch.admin` | + gestão de catálogo, escopos, aprovações |

## Critérios de aceite

- [x] Nenhuma ação operacional possível fora da API (sem links/instruções de SSH) —
  `src/api/client.ts` é o único ponto de contato com o backend, sempre HTTP.
- [x] Fluxo de reprocesso com dupla confirmação de data implementado — `ExecuteModal.tsx`.
  **Testado**: `tsc -b` + `vite build` + `oxlint` limpos (ver `OPERACAO.md`); **não testado** em
  navegador de verdade (sem headless disponível neste ambiente — mesma classe de limitação já
  registrada para o container `docker/legacy`).
- [ ] Reprocesso Zinli/MFTech executável fim-a-fim pela UI — depende do mesmo bloqueio da Etapa 1.2
  (container `docker/legacy` de pé) mais o teste manual em navegador real; é o marco de conclusão
  da Fase 1 (`ROADMAP.md`).
