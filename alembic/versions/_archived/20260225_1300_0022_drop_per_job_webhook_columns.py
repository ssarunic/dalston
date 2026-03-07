"""Drop per-job webhook columns from jobs table.

Per-job webhooks have been removed in favor of admin-registered webhook
endpoints. The webhook_url and webhook_metadata columns are no longer used.

Revision ID: 0022
Revises: 0021
Create Date: 2026-02-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("jobs", "webhook_url")
    op.drop_column("jobs", "webhook_metadata")


def downgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("webhook_metadata", JSONB, nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column("webhook_url", sa.Text(), nullable=True),
    )
