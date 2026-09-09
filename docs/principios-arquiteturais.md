# Princípios Arquiteturais

Critérios de desempate em qualquer decisão de design, válidos nas 3 fases:

1. **Contrato JSON como interface estável.** Nenhuma fase exige reescrita em massa. Extensões aditivas e versionadas (`schema_version`).
2. **Strangler fig, não big bang.** O novo sistema envolve o legado e absorve responsabilidades por domínio/wave. Cron e `main.sh` operam até a última wave da Fase 2.
3. **Execução como dado.** Toda execução gera registro estruturado (quem, quando, processo, steps, data-alvo, resultado, logs vinculados). Estabelecido na Fase 1, retido permanentemente.
4. **Nenhum acesso humano direto ao plano de execução.** Desde a Fase 1, operar job é via Back Office, com Entra ID + role. SSH vira break-glass auditado.
5. **Segredos em vault, não em arquivos.** `connections.json` migra para secrets manager; executor resolve aliases em runtime.
6. **Observabilidade nativa, desacoplada de arquivos.** Grafana Cloud (Loki/Mimir) permanece; muda a fonte: logs estruturados com `execution_id`, métricas do executor, alertas para execuções *ausentes*.
7. **Idempotência e reprocessamento first-class.** `--dates-pattern-files` + `--manual-steps` viram API formal, com validação de data-alvo e salvaguardas contra reenvio acidental a clientes.
