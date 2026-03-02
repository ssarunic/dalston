"""Extend models.source column length.

Revision ID: 0027
Revises: 0026
Create Date: 2026-03-02

The source column stores HuggingFace repo IDs (e.g., "org/model-name")
which can exceed 50 characters. Extend to 200 to match the id column.
"""

import sqlalchemy as sa

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "models",
        "source",
        type_=sa.String(200),
        existing_type=sa.String(50),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "models",
        "source",
        type_=sa.String(50),
        existing_type=sa.String(200),
        existing_nullable=True,
    )
