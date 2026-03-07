"""Add display_name column to jobs table.

Human-readable label for jobs, auto-populated from filename/URL or user-provided.

Revision ID: 0028
Revises: 0027
Create Date: 2026-03-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("display_name", sa.String(255), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("jobs", "display_name")
