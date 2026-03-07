"""Create retention_policies table and seed system policies.

Revision ID: 0007
Revises: 0006
Create Date: 2026-02-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# Revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create retention_policies table
    op.create_table(
        "retention_policies",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,  # NULL for system policies
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),  # auto_delete, keep, none
        sa.Column("hours", sa.Integer(), nullable=True),  # NULL for keep/none
        sa.Column(
            "scope", sa.String(20), nullable=False, server_default="all"
        ),  # all, audio_only
        sa.Column(
            "realtime_mode", sa.String(20), nullable=False, server_default="inherit"
        ),
        sa.Column("realtime_hours", sa.Integer(), nullable=True),
        sa.Column(
            "delete_realtime_on_enhancement",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
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
        # Constraints
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
    )

    # Unique constraint: (tenant_id, name) with NULLS NOT DISTINCT
    # This ensures unique names per tenant, and unique names among system policies
    op.create_index(
        "ix_retention_policies_tenant_name",
        "retention_policies",
        ["tenant_id", "name"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )

    # Index for tenant lookup
    op.create_index(
        "ix_retention_policies_tenant_id",
        "retention_policies",
        ["tenant_id"],
    )

    # Seed system policies (tenant_id = NULL, is_system = true)
    op.execute(
        """
        INSERT INTO retention_policies (id, tenant_id, name, mode, hours, scope, is_system)
        VALUES
            ('00000000-0000-0000-0000-000000000001', NULL, 'default', 'auto_delete', 24, 'all', true),
            ('00000000-0000-0000-0000-000000000002', NULL, 'zero-retention', 'none', NULL, 'all', true),
            ('00000000-0000-0000-0000-000000000003', NULL, 'keep', 'keep', NULL, 'all', true)
        """
    )


def downgrade() -> None:
    op.drop_index("ix_retention_policies_tenant_id", table_name="retention_policies")
    op.drop_index("ix_retention_policies_tenant_name", table_name="retention_policies")
    op.drop_table("retention_policies")
