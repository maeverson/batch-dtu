# Riscos e Mitigações

| Risco | Impacto | Mitigação |
|---|---|---|
| Discrepância comportamental `main.sh` × executor v3 (paridade incompleta) | Arquivos corrompidos/faltantes para clientes e bandeiras | Suíte de testes de contrato + shadow execution com diff de artefatos antes do cutover |
| Reenvio não intencional de arquivo a cliente durante reprocesso | Incidente com cliente; retrabalho de conciliação | Dupla confirmação de data-alvo (Fase 1) e aprovação two-person para steps de upload (Fase 3) |
| Jobs "fantasma": agendas não mapeadas (crontab de root, outros hosts, triggers externos) | Catalogação incompleta do parque | Auditoria de crontab em todos os usuários/hosts + monitoramento de execuções sem `execution_id` conhecido antes do code freeze |
| Dependência de IP estático / allowlist de cliente ao mover executores | Falhas de conexão SFTP pós-migração | Egress via NAT com IP estático dedicado ou jump host leve; notificação prévia a clientes por wave |
| Perda de conhecimento operacional na transição | Aumento de MTTR | Transferência de conhecimento contínua (em curso com o novo owner); runbooks migrados para workflows acionáveis no Back Office |
| Scripts locais (`custom_scripts/`) com dependências ocultas | Bloqueio na Wave 5 | Inventário de scripts e mapeamento de dependências como pré-requisito da Wave 5 |
