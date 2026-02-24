"""Change retention column from string to integer.

This migration converts the retention field from string format ("none", "30d", "forever")
to integer format (0=transient, -1=permanent, N=days).

Revision ID: 0020
Revises: 0019
Create Date: 2026-02-25
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add new integer column to jobs
    op.add_column(
        "jobs",
        sa.Column("retention_days", sa.Integer(), nullable=True),
    )

    # Migrate jobs data: "none" -> 0, "forever" -> -1, "{N}d" -> N
    op.execute("""
        UPDATE jobs SET retention_days = CASE
            WHEN retention = 'none' THEN 0
            WHEN retention = 'forever' THEN -1
            WHEN retention ~ '^[0-9]+d$' THEN CAST(REPLACE(retention, 'd', '') AS INTEGER)
            ELSE 30
        END
    """)

    # Make column non-nullable with default
    op.alter_column(
        "jobs",
        "retention_days",
        nullable=False,
        server_default="30",
    )

    # Drop old string column
    op.drop_column("jobs", "retention")

    # Rename new column to retention
    op.alter_column("jobs", "retention_days", new_column_name="retention")

    # Add new integer column to realtime_sessions
    op.add_column(
        "realtime_sessions",
        sa.Column("retention_days", sa.Integer(), nullable=True),
    )

    # Migrate realtime_sessions data
    op.execute("""
        UPDATE realtime_sessions SET retention_days = CASE
            WHEN retention = 'none' THEN 0
            WHEN retention = 'forever' THEN -1
            WHEN retention ~ '^[0-9]+d$' THEN CAST(REPLACE(retention, 'd', '') AS INTEGER)
            ELSE 30
        END
    """)

    # Make column non-nullable with default
    op.alter_column(
        "realtime_sessions",
        "retention_days",
        nullable=False,
        server_default="30",
    )

    # Drop old string column
    op.drop_column("realtime_sessions", "retention")

    # Rename new column to retention
    op.alter_column("realtime_sessions", "retention_days", new_column_name="retention")


def downgrade() -> None:
    # Add string column back to jobs
    op.add_column(
        "jobs",
        sa.Column("retention_str", sa.String(20), nullable=True),
    )

    # Convert back: 0 -> "none", -1 -> "forever", N -> "{N}d"
    op.execute("""
        UPDATE jobs SET retention_str = CASE
            WHEN retention = 0 THEN 'none'
            WHEN retention = -1 THEN 'forever'
            ELSE retention || 'd'
        END
    """)

    op.alter_column(
        "jobs",
        "retention_str",
        nullable=False,
        server_default="'30d'",
    )

    op.drop_column("jobs", "retention")
    op.alter_column("jobs", "retention_str", new_column_name="retention")

    # Add string column back to realtime_sessions
    op.add_column(
        "realtime_sessions",
        sa.Column("retention_str", sa.String(20), nullable=True),
    )

    op.execute("""
        UPDATE realtime_sessions SET retention_str = CASE
            WHEN retention = 0 THEN 'none'
            WHEN retention = -1 THEN 'forever'
            ELSE retention || 'd'
        END
    """)

    op.alter_column(
        "realtime_sessions",
        "retention_str",
        nullable=False,
        server_default="'30d'",
    )

    op.drop_column("realtime_sessions", "retention")
    op.alter_column("realtime_sessions", "retention_str", new_column_name="retention")
