# Módulo: Managed Scheduler

Contexto ao trabalhar aqui:
- Substitui o crontab. Cada entrada ativa do catálogo gera um schedule no serviço gerenciado (candidato: Amazon EventBridge Scheduler — ver ADR-001).
- O scheduler **não executa nada** — apenas dispara o orchestrator. Nunca acople lógica de execução aqui.
- Fonte das agendas é sempre o catálogo (sync unidirecional catálogo → scheduler).
