# SPEC — Executor v3

## Requisitos de paridade (obrigatórios)

- Funções de step idênticas: `download`/`upload` (SFTP, S3, Azure), `execute_command` (restrito a hosts/scripts registrados — ADR-004), `download_remote`/`upload_remote` (jump server), `encrypt`/`decrypt` (GPG), `copy_local`, `send_mail`/`send_infra_mail`.
- Placeholders de data (`@@@YYYY@@@`, `@@@JULIANO@@@`, `@@@YYYYMMDD@@@`, …) resolvidos contra a data-alvo.
- Semântica `stop_on_failed` por step.
- Seleção manual de steps e execução multi-data serializada.

## Suíte de testes de contrato

Construída dos padrões do inventário; padrões mínimos:
- **Fechamento Base2** (`stop_on_failed=true`): falha interrompe.
- **MFT bulk Sodexo** (78 steps tolerantes a falha): steps continuam com falhas individuais.

Roda contra `main.sh` e Executor v3; paridade byte/linha dos artefatos.

## Shadow execution

Para waves críticas (obrigatória em `/base2`): v3 roda em paralelo, output em quarentena, diff de artefatos por N ciclos antes do cutover.

## Observabilidade

- Logs estruturados JSON com `execution_id` (correlaciona API → Orchestrator → Executor → Loki).
- Métricas por execução: duração, resultado, bytes/arquivos transferidos, retries.

## Conectores

Módulos com least privilege por alias; GPG com monitoramento de validade de chaves de cliente (alerta proativo de expiração).

## Critérios de aceite

- [ ] Suíte de contrato verde contra os dois executores.
- [ ] Shadow runs com paridade em `/base2` antes do cutover.
- [ ] Nenhuma leitura de `connections.json` (somente vault) após a janela de dual-read.
