"""Add created_by_key_id for ownership tracking (M45 Phase 3).

Adds created_by_key_id column to jobs, realtime_sessions, webhook_endpoints,
and api_keys tables for resource ownership enforcement.

Revision ID: 0029
Revises: 0028
Create Date: 2026-03-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Jobs table - track which API key created the job
    op.add_column(
        "jobs",
        sa.Column(
            "created_by_key_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_jobs_created_by_key_id",
        "jobs",
        "api_keys",
        ["created_by_key_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_jobs_created_by_key_id",
        "jobs",
        ["created_by_key_id"],
    )

    # Realtime sessions table - track which API key created the session
    op.add_column(
        "realtime_sessions",
        sa.Column(
            "created_by_key_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_realtime_sessions_created_by_key_id",
        "realtime_sessions",
        "api_keys",
        ["created_by_key_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_realtime_sessions_created_by_key_id",
        "realtime_sessions",
        ["created_by_key_id"],
    )

    # Webhook endpoints table - track which API key created the endpoint
    op.add_column(
        "webhook_endpoints",
        sa.Column(
            "created_by_key_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_webhook_endpoints_created_by_key_id",
        "webhook_endpoints",
        "api_keys",
        ["created_by_key_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_webhook_endpoints_created_by_key_id",
        "webhook_endpoints",
        ["created_by_key_id"],
    )

    # API keys table - track which API key created this key (parentage)
    op.add_column(
        "api_keys",
        sa.Column(
            "created_by_key_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_api_keys_created_by_key_id",
        "api_keys",
        "api_keys",
        ["created_by_key_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_api_keys_created_by_key_id",
        "api_keys",
        ["created_by_key_id"],
    )


def downgrade() -> None:
    # API keys
    op.drop_index("ix_api_keys_created_by_key_id", table_name="api_keys")
    op.drop_constraint("fk_api_keys_created_by_key_id", "api_keys", type_="foreignkey")
    op.drop_column("api_keys", "created_by_key_id")

    # Webhook endpoints
    op.drop_index(
        "ix_webhook_endpoints_created_by_key_id", table_name="webhook_endpoints"
    )
    op.drop_constraint(
        "fk_webhook_endpoints_created_by_key_id", "webhook_endpoints", type_="foreignkey"
    )
    op.drop_column("webhook_endpoints", "created_by_key_id")

    # Realtime sessions
    op.drop_index(
        "ix_realtime_sessions_created_by_key_id", table_name="realtime_sessions"
    )
    op.drop_constraint(
        "fk_realtime_sessions_created_by_key_id",
        "realtime_sessions",
        type_="foreignkey",
    )
    op.drop_column("realtime_sessions", "created_by_key_id")

    # Jobs
    op.drop_index("ix_jobs_created_by_key_id", table_name="jobs")
    op.drop_constraint("fk_jobs_created_by_key_id", "jobs", type_="foreignkey")
    op.drop_column("jobs", "created_by_key_id")
