"""Add settings table for admin-configurable overrides.

Settings are organized by namespace (rate_limits, engines, audio, retention,
webhooks). Database values override environment variable defaults at runtime.

Revision ID: 0021
Revises: 0020
Create Date: 2026-02-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column(
            "id",
            PG_UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column(
            "tenant_id",
            PG_UUID(as_uuid=True),
            sa.ForeignKey("tenants.id"),
            nullable=True,
        ),
        sa.Column("namespace", sa.String(50), nullable=False),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("value", JSONB, nullable=False),
        sa.Column("updated_by", PG_UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "tenant_id", "namespace", "key", name="uq_settings_tenant_ns_key"
        ),
    )
    op.create_index("ix_settings_tenant_id", "settings", ["tenant_id"])
    op.create_index("ix_settings_namespace", "settings", ["namespace"])
    op.create_index(
        "ix_settings_tenant_namespace", "settings", ["tenant_id", "namespace"]
    )


def downgrade() -> None:
    op.drop_index("ix_settings_tenant_namespace", table_name="settings")
    op.drop_index("ix_settings_namespace", table_name="settings")
    op.drop_index("ix_settings_tenant_id", table_name="settings")
    op.drop_table("settings")
