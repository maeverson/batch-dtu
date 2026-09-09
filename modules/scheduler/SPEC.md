# SPEC — Managed Scheduler

## Requisitos

1. **Sync catálogo → scheduler**: cada job ativo com agenda gera schedule gerenciado com **timezone explícita**, janela e **catch-up policy definida** (ação se a plataforma estava indisponível no horário: skip, run-once, run-all).
2. **Importador** das agendas do crontab (via catálogo) na Fase 2.
3. **Estados**: pausar/retomar schedule individual (mecânica de rollback por wave: pausar gerenciado + reativar crontab).
4. **Fase 3 — triggers de evento**: além de horário, disparo por chegada de arquivo (eventos S3 nativos; watchers SFTP), substituindo jobs de polling (dominantes em `/reportes` e `/base2`).
5. **Drift detection**: divergência catálogo × schedules gera alerta (nunca corrigir silenciosamente).

## Critérios de aceite

- [ ] 100% dos jobs ativos agendados fora do crontab (critério da Fase 2).
- [ ] Catch-up policy testada em simulação de indisponibilidade.
- [ ] Rollback (pausar/reativar) validado em pelo menos uma wave.
