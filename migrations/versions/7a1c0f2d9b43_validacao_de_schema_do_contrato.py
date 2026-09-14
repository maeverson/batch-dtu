"""validacao de schema do contrato na escrita

Guarda o veredito do validador (src/catalog/contract_schema.py) junto da versão
do contrato. Fica aqui, e não no job, porque a versão é imutável: o veredito é
o que valia no momento da escrita.

Revision ID: 7a1c0f2d9b43
Revises: 32bcec64786a
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '7a1c0f2d9b43'
down_revision: Union[str, Sequence[str], None] = '32bcec64786a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = "catalog"


def upgrade() -> None:
    # Linhas pré-existentes ficam 'unknown': foram escritas antes de existir
    # validador, e afirmar 'valid' sobre elas seria inventar trilha.
    op.add_column(
        "job_contract_version",
        sa.Column("validation_status", sa.String(length=24), nullable=False,
                  server_default="unknown"),
        schema=SCHEMA,
    )
    op.add_column(
        "job_contract_version",
        sa.Column("validation", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "validation_status_valido",
        "job_contract_version",
        "validation_status in ('valid','valid_with_warnings','invalid','unknown')",
        schema=SCHEMA,
    )
    op.create_index(
        "ix_job_contract_version_validation_status",
        "job_contract_version",
        ["validation_status"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_job_contract_version_validation_status",
                  table_name="job_contract_version", schema=SCHEMA)
    op.drop_constraint("validation_status_valido", "job_contract_version",
                       type_="check", schema=SCHEMA)
    op.drop_column("job_contract_version", "validation", schema=SCHEMA)
    op.drop_column("job_contract_version", "validation_status", schema=SCHEMA)
