"""Drop obsolete hybrid-mode column from realtime sessions.

Revision ID: 0002_drop_realtime_enhance_on_end
Revises: squash_0038
Create Date: 2026-03-11
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_drop_realtime_enhance_on_end"
down_revision: Union[str, None] = "squash_0038"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns(table_name)}
    return column_name in columns


def upgrade() -> None:
    if _has_column("realtime_sessions", "enhance_on_end"):
        with op.batch_alter_table("realtime_sessions") as batch_op:
            batch_op.drop_column("enhance_on_end")


def downgrade() -> None:
    if not _has_column("realtime_sessions", "enhance_on_end"):
        with op.batch_alter_table("realtime_sessions") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "enhance_on_end",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                )
            )
