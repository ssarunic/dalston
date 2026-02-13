"""Add retention columns to jobs and realtime_sessions tables.

Revision ID: 0008
Revises: 0007
Create Date: 2026-02-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# Revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add retention columns to jobs table
    op.add_column(
        "jobs",
        sa.Column(
            "retention_policy_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "retention_mode",
            sa.String(20),
            nullable=False,
            server_default="auto_delete",
        ),
    )
    op.add_column(
        "jobs",
        sa.Column("retention_hours", sa.Integer(), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "retention_scope",
            sa.String(20),
            nullable=False,
            server_default="all",
        ),
    )
    op.add_column(
        "jobs",
        sa.Column("purge_after", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column("purged_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    # Add foreign key constraint for retention_policy_id
    op.create_foreign_key(
        "fk_jobs_retention_policy_id",
        "jobs",
        "retention_policies",
        ["retention_policy_id"],
        ["id"],
    )

    # Index for cleanup worker queries (find jobs to purge)
    op.create_index(
        "ix_jobs_purge_after",
        "jobs",
        ["purge_after"],
        postgresql_where=sa.text("purge_after IS NOT NULL AND purged_at IS NULL"),
    )

    # Add retention columns to realtime_sessions table
    op.add_column(
        "realtime_sessions",
        sa.Column(
            "retention_policy_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "realtime_sessions",
        sa.Column(
            "retention_mode",
            sa.String(20),
            nullable=False,
            server_default="auto_delete",
        ),
    )
    op.add_column(
        "realtime_sessions",
        sa.Column("retention_hours", sa.Integer(), nullable=True),
    )
    op.add_column(
        "realtime_sessions",
        sa.Column("purge_after", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "realtime_sessions",
        sa.Column("purged_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )

    # Add foreign key constraint for realtime_sessions retention_policy_id
    op.create_foreign_key(
        "fk_realtime_sessions_retention_policy_id",
        "realtime_sessions",
        "retention_policies",
        ["retention_policy_id"],
        ["id"],
    )

    # Index for cleanup worker queries (find sessions to purge)
    op.create_index(
        "ix_realtime_sessions_purge_after",
        "realtime_sessions",
        ["purge_after"],
        postgresql_where=sa.text("purge_after IS NOT NULL AND purged_at IS NULL"),
    )


def downgrade() -> None:
    # Drop realtime_sessions columns
    op.drop_index(
        "ix_realtime_sessions_purge_after", table_name="realtime_sessions"
    )
    op.drop_constraint(
        "fk_realtime_sessions_retention_policy_id",
        "realtime_sessions",
        type_="foreignkey",
    )
    op.drop_column("realtime_sessions", "purged_at")
    op.drop_column("realtime_sessions", "purge_after")
    op.drop_column("realtime_sessions", "retention_hours")
    op.drop_column("realtime_sessions", "retention_mode")
    op.drop_column("realtime_sessions", "retention_policy_id")

    # Drop jobs columns
    op.drop_index("ix_jobs_purge_after", table_name="jobs")
    op.drop_constraint("fk_jobs_retention_policy_id", "jobs", type_="foreignkey")
    op.drop_column("jobs", "purged_at")
    op.drop_column("jobs", "purge_after")
    op.drop_column("jobs", "retention_scope")
    op.drop_column("jobs", "retention_hours")
    op.drop_column("jobs", "retention_mode")
    op.drop_column("jobs", "retention_policy_id")
