# SPEC — Orchestrator

## Requisitos

1. **Fila de execução** com prioridade por criticidade do catálogo.
2. **Concorrência por recurso**: serialização declarativa por destino (mesmo SFTP), por cliente e por processo (reprocesso multi-data é sempre sequencial).
3. **Retries com backoff** configuráveis por job; timeouts por execução e por step.
4. **DAGs (Fase 3)**: dependências explícitas entre jobs — cadeias hoje implícitas por horários escalonados (ex.: `parte1` 08:45 → `parte2` 09:15 em emissão) viram "dispara na conclusão bem-sucedida do anterior".
5. **Gatilhos**: scheduler, manual (API), reprocesso, evento (Fase 3), dependência (Fase 3).
6. **Registro**: um `execution` por invocação com trigger, steps, `dates_pattern`, worker, resultado, link de log.
7. **Fase 3 — multi-tenancy**: limites de concorrência por destino/cliente declarativos; isolamento de compute por ambiente (PROD/UAT/TEST).

## Critérios de aceite

- [ ] Teste demonstrando serialização contra o mesmo destino SFTP sob carga.
- [ ] Retry com backoff e timeout verificados em testes de integração.
- [ ] Execução reproduzível de qualquer worker (sem estado exclusivo de host).
