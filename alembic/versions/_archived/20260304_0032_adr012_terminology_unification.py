"""ADR-012 Terminology Unification.

Revision ID: 0032
Revises: 0031
Create Date: 2026-03-04

Standardize terminology per ADR-012:
- tasks.engine_id -> tasks.runtime
- realtime_sessions.engine -> realtime_sessions.runtime
- realtime_sessions.worker_id -> realtime_sessions.instance
"""

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Batch: engine_id -> runtime
    op.alter_column("tasks", "engine_id", new_column_name="runtime")

    # RT: engine -> runtime (was already using runtime in M43, now make it official)
    op.alter_column("realtime_sessions", "engine", new_column_name="runtime")

    # RT: worker_id -> instance
    op.alter_column("realtime_sessions", "worker_id", new_column_name="instance")


def downgrade() -> None:
    op.alter_column("realtime_sessions", "instance", new_column_name="worker_id")
    op.alter_column("realtime_sessions", "runtime", new_column_name="engine")
    op.alter_column("tasks", "runtime", new_column_name="engine_id")
