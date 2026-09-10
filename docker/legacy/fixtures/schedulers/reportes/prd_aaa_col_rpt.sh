#!/bin/bash
# wrapper gerado para o ambiente de desenvolvimento
FW=/opt2/batch_v2/batch-commons-framework
bash $FW/main.sh --process-file $FW/processes/reportes/prd_aaa_col_rpt.json --no-mail
