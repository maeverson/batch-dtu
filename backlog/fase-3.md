# Fase 3 — Evolução (Event-Driven)

Com a plataforma estabilizada, muda o paradigma: de "rodar no horário" para "reagir a eventos de negócio".

## Entregas

- **Execução orientada a eventos** — jobs que hoje fazem polling até arquivo chegar (dominantes em `/reportes` e `/base2`) passam de horário fixo para triggers de evento: chegada de arquivo em S3 (eventos nativos) ou SFTP (watchers). Reduz latência fim-a-fim e elimina o modo de falha "rodou antes do arquivo existir".
- **Dependências e DAGs** — cadeias implícitas geridas por horários escalonados (ex.: `parte1` 08:45 → `parte2` 09:15 em emissão) viram dependências explícitas: step 2 dispara estritamente na conclusão bem-sucedida do step 1.
- **SLAs e alertas de atraso** — cada job crítico do catálogo recebe SLA (ex.: "Base2 entregue até 10:00"); a plataforma alerta atrasos e execuções ausentes.
- **Reprocessamento self-service com aprovações** — operações/suporte solicitam pelo Back Office; steps que reenviam arquivo a cliente exigem aprovação `batch.admin` (two-person rule) com auditabilidade fim-a-fim. O SOP Zinli vira workflow governado na UI.
- **Onboarding no-code de jobs** — criar job = gerar contrato JSON validado por schema no Back Office (wizard por arquétipos: fechamento, MFT, relatórios), com promoção versionada UAT → PROD. `--validate-file` vira validação contínua de todo o catálogo.
- **Multi-tenancy e isolamento** — limites declarativos de concorrência por destino/cliente ("não paralelizar contra o mesmo SFTP") + isolamento físico de ambientes: PROD, UAT e TEST deixam de compartilhar compute.
- **FinOps e resiliência** — right-sizing por job (substituir heap fixo de 2 GB por connectors nativos ou memória perfilada), scale-to-zero em janelas ociosas, DR robusto: como todo estado vive em catálogo + vault + banco de execução, o plano de execução é recriável via IaC em qualquer região/AZ.
