"""Add ready_at timestamp to tasks table.

Tracks when a task transitions to READY status (all dependencies met,
enqueued for processing). Enables separating queue wait time from
actual engine processing time.

Revision ID: 0008_add_task_ready_at
Revises: 0007_rename_task_input_output_uri
Create Date: 2026-04-05
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008_add_task_ready_at"
down_revision: str = "0007_rename_task_input_output_uri"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.add_column(
            sa.Column("ready_at", sa.TIMESTAMP(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_column("ready_at")
