# Fase 2 — API de Execução/Orchestrator + Remoção da Dependência de Servidor

**Objetivo**: substituir cron + `main.sh` por scheduler gerenciado + executor containerizado, mantendo intactos o contrato JSON e a API da Fase 1, e descomissionar o host `Batch-Prod-srv-sftp-2-120` (`172.17.37.120`).

## Entregas técnicas

1. **Executor v3** — serviço containerizado com paridade funcional com `main.sh` (funções de step, placeholders de data, `stop_on_failed`, `send_infra_mail`, seleção manual de steps). Paridade verificada por suíte de testes de contrato construída dos padrões do inventário (fechamento Base2 com `stop_on_failed=true`; MFT bulk Sodexo com 78 steps tolerantes a falha).
2. **Migração de segredos** — `connections.json` → vault, alias por alias, com janela de dual-read (v3 lê do vault; legado segue lendo do arquivo) e checagens de consistência automatizadas (evolução do `validate_connections_json.sh`).
3. **Scheduler** — agendas do crontab importadas ao scheduler gerenciado **a partir do catálogo** — catálogo como fonte única de agendamento.
4. **Migração por waves** (ver `docs/migracao-e-compatibilidade.md`), concluindo com descomissionamento. Jobs com `execute_command` dependentes de `custom_scripts/` ficam para a wave final.

## Critérios de aceite da fase

- [ ] 100% dos jobs ativos agendados fora do crontab.
- [ ] Execução reproduzível de qualquer worker (sem estado exclusivo de host).
- [ ] Servidores legados desligados ou reduzidos a jump hosts finos para destinos que exigem IP de origem estático.
- [ ] RTO da plataforma validado em drill de disaster recovery (reconstrução do plano de execução do zero).
