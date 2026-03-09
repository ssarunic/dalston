"""Add management column to models table.

Supports external model lifecycle management (e.g., Riva NIM containers
manage their own models — Dalston seeds them as ready and skips download).

Revision ID: 0039_mgmt
Revises: squash_0038
Create Date: 2026-03-09
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0039_mgmt"
down_revision: Union[str, None] = "squash_0038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("models") as batch_op:
        batch_op.add_column(
            sa.Column(
                "management",
                sa.String(20),
                nullable=False,
                server_default="dalston",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("models") as batch_op:
        batch_op.drop_column("management")
