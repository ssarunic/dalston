"""Drop legacy retention fields from M25.

Removes retention_policy_id, retention_mode, retention_hours, retention_scope
from jobs and realtime_sessions tables. Also drops the retention_policies table.

The simplified retention system uses integer retention days directly (0=transient,
-1=permanent, N=days) with purge_after timestamps.

Revision ID: 0030
Revises: 0029
Create Date: 2026-03-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop FK constraints first
    op.drop_constraint(
        "jobs_retention_policy_id_fkey", "jobs", type_="foreignkey"
    )
    op.drop_constraint(
        "realtime_sessions_retention_policy_id_fkey",
        "realtime_sessions",
        type_="foreignkey",
    )

    # Drop legacy columns from jobs
    op.drop_column("jobs", "retention_policy_id")
    op.drop_column("jobs", "retention_mode")
    op.drop_column("jobs", "retention_hours")
    op.drop_column("jobs", "retention_scope")

    # Drop legacy columns from realtime_sessions
    op.drop_column("realtime_sessions", "retention_policy_id")
    op.drop_column("realtime_sessions", "retention_mode")
    op.drop_column("realtime_sessions", "retention_hours")

    # Drop retention_policies table (no longer used)
    op.drop_table("retention_policies")


def downgrade() -> None:
    # Recreate retention_policies table
    op.create_table(
        "retention_policies",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("hours", sa.Integer(), nullable=True),
        sa.Column("scope", sa.String(20), server_default="all", nullable=False),
        sa.Column(
            "realtime_mode", sa.String(20), server_default="inherit", nullable=False
        ),
        sa.Column("realtime_hours", sa.Integer(), nullable=True),
        sa.Column(
            "delete_realtime_on_enhancement",
            sa.Boolean(),
            server_default="true",
            nullable=False,
        ),
        sa.Column(
            "is_system", sa.Boolean(), server_default="false", nullable=False
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_retention_policies_tenant_id",
        "retention_policies",
        ["tenant_id"],
    )

    # Restore legacy columns on realtime_sessions
    op.add_column(
        "realtime_sessions",
        sa.Column("retention_hours", sa.Integer(), nullable=True),
    )
    op.add_column(
        "realtime_sessions",
        sa.Column(
            "retention_mode",
            sa.String(20),
            server_default="auto_delete",
            nullable=False,
        ),
    )
    op.add_column(
        "realtime_sessions",
        sa.Column(
            "retention_policy_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
    )
    op.create_foreign_key(
        "realtime_sessions_retention_policy_id_fkey",
        "realtime_sessions",
        "retention_policies",
        ["retention_policy_id"],
        ["id"],
    )

    # Restore legacy columns on jobs
    op.add_column(
        "jobs",
        sa.Column(
            "retention_scope", sa.String(20), server_default="all", nullable=False
        ),
    )
    op.add_column(
        "jobs",
        sa.Column("retention_hours", sa.Integer(), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "retention_mode",
            sa.String(20),
            server_default="auto_delete",
            nullable=False,
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "retention_policy_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
    )
    op.create_foreign_key(
        "jobs_retention_policy_id_fkey",
        "jobs",
        "retention_policies",
        ["retention_policy_id"],
        ["id"],
    )
