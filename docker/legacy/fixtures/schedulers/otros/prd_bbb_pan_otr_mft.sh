#!/bin/bash
# wrapper gerado para o ambiente de desenvolvimento
FW=/opt2/batch_v2/batch-commons-framework
bash $FW/main.sh --process-file $FW/processes/otros/prd_bbb_pan_otr_mft.json --no-mail
