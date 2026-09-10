"""Configuração do Alembic para o schema `catalog`.

Duas coisas que não são default e importam:

* as migrations rodam como `batch_migrator` (dona do schema), nunca como a
  role da aplicação — é o que permite que `batch_app` fique sem UPDATE/DELETE
  nas tabelas append-only;
* `include_schemas`/`version_table_schema` mantêm a tabela de versão dentro do
  próprio schema, para o banco não ter estado do Alembic solto no `public`.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from catalog.db.models import SCHEMA, Base
from catalog.db.session import database_url

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", database_url("migrations"))
target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):
    """Ignora as partições de `audit_event`: são criadas por DDL da migration,
    não pelo modelo, e apareceriam como drift a cada autogenerate."""
    if type_ == "table" and name.startswith("audit_event_"):
        return False
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_schemas=True,
        version_table_schema=SCHEMA,
        include_object=include_object,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            version_table_schema=SCHEMA,
            include_object=include_object,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
