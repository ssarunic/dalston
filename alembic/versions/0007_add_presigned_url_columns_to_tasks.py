"""Add presigned URL columns to tasks table (M77).

Stores the presigned GET URL for input.json and the presigned PUT URL for
output.json alongside each task row. Nullable so existing rows continue to
work on the old code path until M77 engines are fully deployed.

Revision ID: 0007_add_presigned_url_columns_to_tasks
Revises: 0006_rename_streaming_to_native_streaming
Create Date: 2026-03-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007_add_presigned_url_columns_to_tasks"
down_revision: str = "0006_rename_streaming_to_native_streaming"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("input_json_url", sa.Text(), nullable=True))
    op.add_column("tasks", sa.Column("output_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("tasks", "output_url")
    op.drop_column("tasks", "input_json_url")
