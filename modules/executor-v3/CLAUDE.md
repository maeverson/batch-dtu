# Módulo: Executor v3

Contexto ao trabalhar aqui:
- Reimplementação de `main.sh` como serviço interpretando **o mesmo contrato JSON** (leia `../../docs/contrato-json.md` antes de tudo). Runtime em aberto (ADR-002).
- Roda em containers efêmeros (ex.: ECS Fargate), escala horizontal, cada execução isolada — elimina a contenção de heap fixo (2 GB) dos JARs e o estado local de servidor.
- **Paridade funcional é o requisito nº 1.** Qualquer divergência de comportamento vs. `main.sh` é bug, mesmo que o comportamento legado pareça errado — mudanças passam por decisão explícita.
- Segredos: resolve aliases no vault em runtime; nunca lê arquivos de credenciais.
