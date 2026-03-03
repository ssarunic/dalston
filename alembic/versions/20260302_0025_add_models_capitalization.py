"""Add capitalization column to models table.

Revision ID: 0025
Revises: 0024
Create Date: 2026-03-02

This migration adds the capitalization capability flag to the models table.
Whisper models DO produce capitalized text, so this was incorrectly omitted.
"""

import sqlalchemy as sa

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "models",
        sa.Column("capitalization", sa.Boolean, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("models", "capitalization")
