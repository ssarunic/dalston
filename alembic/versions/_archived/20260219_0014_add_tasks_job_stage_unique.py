"""Add unique constraint on tasks(job_id, stage).

Prevents duplicate tasks from being created when multiple orchestrators
handle the same job.created event concurrently.

Revision ID: 0014
Revises: 0013
Create Date: 2026-02-19
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# Revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # First, remove any duplicate (job_id, stage) rows that may exist
    # Keep one row per duplicate group (deterministically chosen by id)
    conn = op.get_bind()

    # Find and delete duplicates using ROW_NUMBER to rank by id
    # We keep row_num = 1 and delete all others
    # Note: TaskModel has no created_at column, so we use id for deterministic selection
    conn.execute(
        sa.text("""
            DELETE FROM tasks
            WHERE id IN (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (
                               PARTITION BY job_id, stage
                               ORDER BY id ASC
                           ) as row_num
                    FROM tasks
                ) ranked
                WHERE row_num > 1
            )
        """)
    )

    # Now safe to add the unique constraint
    op.create_unique_constraint(
        "uq_tasks_job_id_stage",
        "tasks",
        ["job_id", "stage"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_tasks_job_id_stage", "tasks", type_="unique")
