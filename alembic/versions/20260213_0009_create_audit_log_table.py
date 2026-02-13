"""Create audit_log table with immutability rules.

Revision ID: 0009
Revises: 0008
Create Date: 2026-02-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# Revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create audit_log table
    op.create_table(
        "audit_log",
        sa.Column(
            "id",
            sa.BigInteger(),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column(
            "timestamp",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("correlation_id", sa.String(36), nullable=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_type", sa.String(20), nullable=False),  # api_key, system, user
        sa.Column("actor_id", sa.Text(), nullable=False),  # key prefix, system name, etc.
        sa.Column("action", sa.String(50), nullable=False),  # job.created, transcript.accessed
        sa.Column("resource_type", sa.String(30), nullable=False),  # job, session, api_key
        sa.Column("resource_id", sa.Text(), nullable=False),  # UUID as string
        sa.Column("detail", postgresql.JSONB(), nullable=True),  # Additional context
        sa.Column("ip_address", postgresql.INET(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        # Primary key
        sa.PrimaryKeyConstraint("id"),
    )

    # Indexes for common query patterns
    op.create_index(
        "ix_audit_log_timestamp",
        "audit_log",
        ["timestamp"],
    )
    op.create_index(
        "ix_audit_log_tenant_id",
        "audit_log",
        ["tenant_id"],
    )
    op.create_index(
        "ix_audit_log_resource",
        "audit_log",
        ["resource_type", "resource_id"],
    )
    op.create_index(
        "ix_audit_log_action",
        "audit_log",
        ["action"],
    )
    op.create_index(
        "ix_audit_log_correlation_id",
        "audit_log",
        ["correlation_id"],
    )

    # Immutability rules: prevent UPDATE and DELETE
    # Note: These rules make the table append-only, which is required for audit compliance
    op.execute(
        """
        CREATE RULE audit_log_no_update AS ON UPDATE TO audit_log DO INSTEAD NOTHING
        """
    )
    op.execute(
        """
        CREATE RULE audit_log_no_delete AS ON DELETE TO audit_log DO INSTEAD NOTHING
        """
    )


def downgrade() -> None:
    # Drop immutability rules first
    op.execute("DROP RULE IF EXISTS audit_log_no_delete ON audit_log")
    op.execute("DROP RULE IF EXISTS audit_log_no_update ON audit_log")

    # Drop indexes
    op.drop_index("ix_audit_log_correlation_id", table_name="audit_log")
    op.drop_index("ix_audit_log_action", table_name="audit_log")
    op.drop_index("ix_audit_log_resource", table_name="audit_log")
    op.drop_index("ix_audit_log_tenant_id", table_name="audit_log")
    op.drop_index("ix_audit_log_timestamp", table_name="audit_log")

    op.drop_table("audit_log")
