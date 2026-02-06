"""Add auto-disable tracking fields to webhook_endpoints.

Revision ID: 0004
Revises: 0003
Create Date: 2026-02-06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# Revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add auto-disable tracking fields
    op.add_column(
        "webhook_endpoints",
        sa.Column("disabled_reason", sa.String(50), nullable=True),
    )
    op.add_column(
        "webhook_endpoints",
        sa.Column(
            "consecutive_failures",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "webhook_endpoints",
        sa.Column("last_success_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("webhook_endpoints", "last_success_at")
    op.drop_column("webhook_endpoints", "consecutive_failures")
    op.drop_column("webhook_endpoints", "disabled_reason")
