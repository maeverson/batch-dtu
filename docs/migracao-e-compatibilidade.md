# Estratégia de Migração e Compatibilidade (Fase 2)

## Waves por domínio (menor → maior risco)

1. `/otros` — utilitários internos, zero exposição a cliente externo.
2. `/reportes` — alto volume, idempotente/re-executável, inclui processos Zinli bem compreendidos.
3. `/saldos` e `/transacciones`.
4. `/emisiones` e `/emboces` — bureaus externos de cartão, janelas de entrega estritas.
5. `/base2` — clearing Visa/Mastercard (maior criticidade), migrado por último com **dual shadow execution**: executor v3 roda em paralelo com output do legado em quarentena, comparando artefatos gerados antes do cutover final.

## Critérios de cutover por job

- [ ] Contrato JSON validado por schema.
- [ ] Shadow runs com paridade byte/linha por N ciclos.
- [ ] Aliases de conexão migrados ao vault.
- [ ] Schedule ativo no scheduler gerenciado e comentado/desabilitado no crontab.

## Rollback

Reativar entrada do crontab + pausar schedule gerenciado. Trivial em todas as waves porque o contrato subjacente é idêntico.

## Legacy freeze

A partir da Fase 2, toda modificação de job **nasce no catálogo**; o crontab vira artefato gerado/reconciliado, nunca editado manualmente sem trilha de auditoria.

## Jobs com scripts locais

Jobs com `execute_command` dependentes de `custom_scripts/` ficam para a wave final: scripts são containerizados junto ao executor ou reescritos como steps nativos. Pré-requisito: inventário de scripts e mapeamento de dependências (auditoria `tree -L 2` em andamento).
