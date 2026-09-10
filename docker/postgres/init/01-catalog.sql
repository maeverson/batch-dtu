-- Inicialização do banco de desenvolvimento do catálogo.
--
-- Roles separadas por papel desde o dia 1, porque a imutabilidade da auditoria
-- (invariante 3 e docs/modelo-de-dados.md) é imposta por privilégio, não por
-- convenção de código: a aplicação NÃO pode ter UPDATE/DELETE nas tabelas
-- append-only, e nenhuma migration deve conceder.
--
-- Em produção (RDS) o equivalente vem do Terraform; aqui é só para o
-- desenvolvimento bater com a topologia real.

-- Role da aplicação (Platform API / importador do catálogo)
CREATE ROLE batch_app LOGIN PASSWORD 'batch_app_dev';

-- Role somente-leitura (dashboards, consultas de auditoria, suporte)
CREATE ROLE batch_readonly LOGIN PASSWORD 'batch_readonly_dev';

-- Role dona do schema, usada apenas pelas migrations
CREATE ROLE batch_migrator LOGIN PASSWORD 'batch_migrator_dev';

GRANT CONNECT ON DATABASE batch_catalog TO batch_app, batch_readonly, batch_migrator;

\connect batch_catalog

CREATE SCHEMA IF NOT EXISTS catalog AUTHORIZATION batch_migrator;

GRANT USAGE ON SCHEMA catalog TO batch_app, batch_readonly;

-- Defaults: tudo que as migrations criarem já nasce com o privilégio certo.
-- A migration é responsável por REVOKE de UPDATE/DELETE nas tabelas
-- append-only (audit_event, job_contract_version, job_revision).
ALTER DEFAULT PRIVILEGES FOR ROLE batch_migrator IN SCHEMA catalog
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO batch_app;
ALTER DEFAULT PRIVILEGES FOR ROLE batch_migrator IN SCHEMA catalog
    GRANT SELECT ON TABLES TO batch_readonly;
ALTER DEFAULT PRIVILEGES FOR ROLE batch_migrator IN SCHEMA catalog
    GRANT USAGE, SELECT ON SEQUENCES TO batch_app;

-- Extensões usadas pelo modelo
CREATE EXTENSION IF NOT EXISTS "pgcrypto";   -- gen_random_uuid() para ids
CREATE EXTENSION IF NOT EXISTS "btree_gin";  -- filtros combinados no catálogo

-- Banco separado para rodar a suíte de testes sem derrubar o de desenvolvimento
CREATE DATABASE batch_catalog_test OWNER batch_migrator;
GRANT CONNECT ON DATABASE batch_catalog_test TO batch_app, batch_readonly;
