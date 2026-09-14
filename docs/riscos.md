# Riscos e Mitigações

| Risco | Impacto | Mitigação |
|---|---|---|
| Discrepância comportamental `main.sh` × executor v3 (paridade incompleta) | Arquivos corrompidos/faltantes para clientes e bandeiras | Suíte de testes de contrato + shadow execution com diff de artefatos antes do cutover |
| Reenvio não intencional de arquivo a cliente durante reprocesso | Incidente com cliente; retrabalho de conciliação | Dupla confirmação de data-alvo (Fase 1) e aprovação two-person para steps de upload (Fase 3) |
| Jobs "fantasma": agendas não mapeadas (crontab de root, triggers externos) | Catalogação incompleta do host em escopo | Auditoria de crontab em todos os usuários do host + monitoramento de execuções sem `execution_id` conhecido antes do code freeze |
| **Jobs em servidores fora do escopo** — confirmado, não hipotético: há agendas em `p-batch-1`, `reportes-130`, `P-MDW-BATCH-1`, `p-mx-batch-1` e `P-MX-SFTP-2` (PROD), nunca coletados | A plataforma cobre `172.17.37.120` e o restante segue sem governança; risco de ser lido como cobertura total | Escopo declarado explicitamente no `CLAUDE.md`; ampliar exige coleta própria por host, revisão do vocabulário e reavaliação das waves |
| **Job declarando PROD no host de UAT** — confirmado: `uat_nuvy_plx_col_ebc` está ativo (2×/dia), o contrato declara `environment: prd`, cliente `sodexo`, e tem step `upload_remote` para `idemia_pluxee_8_10` / `sftp_ext_147_236` | Entrega de arquivo a fornecedor/cliente externo a partir do host de homologação; UAT deixa de ser superfície segura de ensaio | Catalogar o ambiente pelo **contrato** (já é a fonte autoritativa no importador) e tratar os 12 casos de `dimensao-divergente` como fila de correção com o owner; enquanto existirem, `upload_remote` em UAT exige a mesma confirmação reforçada de PROD |
| Dependência de IP estático / allowlist de cliente ao mover executores | Falhas de conexão SFTP pós-migração | Egress via NAT com IP estático dedicado ou jump host leve; notificação prévia a clientes por wave |
| Perda de conhecimento operacional na transição | Aumento de MTTR | Transferência de conhecimento contínua (em curso com o novo owner); runbooks migrados para workflows acionáveis no Back Office |
| Scripts locais (`custom_scripts/`) com dependências ocultas | Bloqueio na Wave 5 | Inventário de scripts e mapeamento de dependências como pré-requisito da Wave 5 |
