"""Drop V2 retention templates system.

This migration removes the V2 retention template system which has been replaced
by the simplified unified retention model using a single `retention` parameter.

Revision ID: 0019
Revises: 0018
Create Date: 2026-02-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop V2 retention columns from jobs (columns may not have FK constraints)
    op.drop_column("jobs", "retention_template_id")
    op.drop_column("jobs", "retention_snapshot")

    # Drop V2 retention columns from realtime_sessions
    op.drop_column("realtime_sessions", "retention_template_id")
    op.drop_column("realtime_sessions", "retention_snapshot")

    # Drop retention_template_rules table first (has FK to retention_templates)
    op.drop_table("retention_template_rules")

    # Drop retention_templates indexes (only drop if exists)
    op.drop_index(
        "ix_retention_templates_tenant_id",
        table_name="retention_templates",
        if_exists=True,
    )

    # Drop retention_templates table
    op.drop_table("retention_templates")


def downgrade() -> None:
    # Recreate retention_templates table
    op.create_table(
        "retention_templates",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "is_system",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
    )

    op.create_index(
        "ix_retention_templates_tenant_name",
        "retention_templates",
        ["tenant_id", "name"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )
    op.create_index(
        "ix_retention_templates_tenant_id",
        "retention_templates",
        ["tenant_id"],
    )

    # Recreate retention_template_rules table
    op.create_table(
        "retention_template_rules",
        sa.Column(
            "template_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("artifact_type", sa.String(50), nullable=False),
        sa.Column("store", sa.Boolean(), nullable=False),
        sa.Column("ttl_seconds", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("template_id", "artifact_type"),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["retention_templates.id"],
            ondelete="CASCADE",
        ),
    )

    # Re-add V2 columns to jobs
    op.add_column(
        "jobs",
        sa.Column("retention_snapshot", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "retention_template_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_jobs_retention_template_id",
        "jobs",
        "retention_templates",
        ["retention_template_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Re-add V2 columns to realtime_sessions
    op.add_column(
        "realtime_sessions",
        sa.Column("retention_snapshot", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "realtime_sessions",
        sa.Column(
            "retention_template_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_realtime_sessions_retention_template_id",
        "realtime_sessions",
        "retention_templates",
        ["retention_template_id"],
        ["id"],
        ondelete="SET NULL",
    )
