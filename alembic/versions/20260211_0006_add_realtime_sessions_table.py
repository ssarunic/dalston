"""Add realtime_sessions table.

Revision ID: 0006
Revises: 0005
Create Date: 2026-02-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# Revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "realtime_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        # Status
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="active",
        ),
        # Parameters
        sa.Column("language", sa.String(10), nullable=True),
        sa.Column("model", sa.String(50), nullable=True),
        sa.Column("encoding", sa.String(20), nullable=True),
        sa.Column("sample_rate", sa.Integer(), nullable=True),
        # Feature flags
        sa.Column(
            "store_audio",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "store_transcript",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "enhance_on_end",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        # Results
        sa.Column("audio_uri", sa.Text(), nullable=True),
        sa.Column("transcript_uri", sa.Text(), nullable=True),
        sa.Column(
            "enhancement_job_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        # Stats
        sa.Column(
            "audio_duration_seconds",
            sa.Float(),
            nullable=False,
            server_default=sa.text("0.0"),
        ),
        sa.Column(
            "utterance_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "word_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        # Tracking
        sa.Column("worker_id", sa.String(100), nullable=True),
        sa.Column("client_ip", sa.String(45), nullable=True),
        sa.Column(
            "previous_session_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        # Timestamps
        sa.Column(
            "started_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("ended_at", sa.TIMESTAMP(timezone=True), nullable=True),
        # Error tracking
        sa.Column("error", sa.Text(), nullable=True),
        # Constraints
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(
            ["enhancement_job_id"],
            ["jobs.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["previous_session_id"],
            ["realtime_sessions.id"],
            ondelete="SET NULL",
        ),
    )
    # Indexes
    op.create_index(
        "ix_realtime_sessions_tenant_id",
        "realtime_sessions",
        ["tenant_id"],
    )
    op.create_index(
        "ix_realtime_sessions_status",
        "realtime_sessions",
        ["status"],
    )
    op.create_index(
        "ix_realtime_sessions_started_at",
        "realtime_sessions",
        ["started_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_realtime_sessions_started_at", table_name="realtime_sessions")
    op.drop_index("ix_realtime_sessions_status", table_name="realtime_sessions")
    op.drop_index("ix_realtime_sessions_tenant_id", table_name="realtime_sessions")
    op.drop_table("realtime_sessions")
