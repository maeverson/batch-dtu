# SPEC — Back Office

## Objetivo

Interface web para navegar o catálogo, disparar execuções manuais e reprocessos, monitorar em tempo real e gerir habilitação de jobs com motivos registrados (substitui os comentários do crontab).

## Funcionalidades

1. **Navegação do catálogo** — lista/busca/filtros (domínio, cliente, país, ambiente, status, criticidade); detalhe do job com contrato JSON, agenda, owner, SLA, histórico de execuções.
2. **Execução manual** — seleção de steps (`--manual-steps`), data(s)-alvo, opção no-mail; pré-validação; **confirmação explícita de data-alvo**; para steps `upload_remote` em PROD, confirmação reforçada (usuário redigita a data).
3. **Reprocesso multi-data** — intervalo de datas com preview da serialização; indicador de que execuções do mesmo processo não paralelizam.
4. **Monitoramento em tempo real** — status da execução, stream de logs (Loki por `execution_id`), resultado por step.
5. **Habilitar/desabilitar** — com `reason` obrigatório; Fase 1 mostra estado de reconciliação (diff crontab × catálogo).
6. **Gestão de janelas** — visualização de janelas/agendas por job.
7. **Fase 3** — fila de aprovações (two-person rule), wizard de onboarding no-code de jobs (arquétipos: fechamento, MFT, relatórios) com promoção UAT → PROD.

## RBAC na UI

| Role | UI |
|---|---|
| `batch.viewer` | somente leitura |
| `batch.operator` | executar/reprocessar em UAT/TEST |
| `batch.operator-prod` | + PROD com confirmação reforçada |
| `batch.admin` | + gestão de catálogo, escopos, aprovações |

## Critérios de aceite

- [ ] Nenhuma ação operacional possível fora da API (sem links/instruções de SSH).
- [ ] Fluxo de reprocesso com dupla confirmação de data implementado e testado.
- [ ] Reprocesso Zinli/MFTech executável fim-a-fim pela UI.
