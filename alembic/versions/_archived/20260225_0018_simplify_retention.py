"""Simplify retention: add unified retention column, remove store_audio/store_transcript.

This migration introduces a single `retention` parameter for both batch jobs and
realtime sessions, replacing the complex V2 retention template system and the
separate store_audio/store_transcript flags.

The retention parameter accepts:
- "none" - transient mode, nothing stored
- "{N}d" - store and keep for N days (e.g., "1d", "30d", "90d")
- "forever" - store and never auto-delete

Revision ID: 0018
Revises: 0017
Create Date: 2026-02-25
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add retention column to jobs table
    op.add_column(
        "jobs",
        sa.Column("retention", sa.String(20), nullable=True),
    )

    # Add retention column to realtime_sessions table
    op.add_column(
        "realtime_sessions",
        sa.Column("retention", sa.String(20), nullable=True),
    )

    # Migrate existing jobs data
    # Priority: retention_hours → retention_mode → default
    op.execute(
        """
        UPDATE jobs
        SET retention = CASE
            -- If retention_hours is set, convert to days
            WHEN retention_hours IS NOT NULL AND retention_hours > 0
                THEN (retention_hours / 24)::text || 'd'
            -- If mode is 'none', use immediate purge
            WHEN retention_mode = 'none'
                THEN 'none'
            -- If mode is 'keep', use forever
            WHEN retention_mode = 'keep'
                THEN 'forever'
            -- Default to 30 days
            ELSE '30d'
        END
        """
    )

    # Migrate existing realtime_sessions data
    # Priority: store flags → retention_hours → retention_mode → default
    op.execute(
        """
        UPDATE realtime_sessions
        SET retention = CASE
            -- If both store flags are false, nothing was stored
            WHEN NOT store_audio AND NOT store_transcript
                THEN 'none'
            -- If retention_hours is set, convert to days
            WHEN retention_hours IS NOT NULL AND retention_hours > 0
                THEN (retention_hours / 24)::text || 'd'
            -- If mode is 'none', use immediate purge
            WHEN retention_mode = 'none'
                THEN 'none'
            -- If mode is 'keep', use forever
            WHEN retention_mode = 'keep'
                THEN 'forever'
            -- Default to 30 days
            ELSE '30d'
        END
        """
    )

    # Set default and make not nullable
    op.alter_column(
        "jobs",
        "retention",
        nullable=False,
        server_default="30d",
    )
    op.alter_column(
        "realtime_sessions",
        "retention",
        nullable=False,
        server_default="30d",
    )

    # Drop store_audio and store_transcript from realtime_sessions
    op.drop_column("realtime_sessions", "store_audio")
    op.drop_column("realtime_sessions", "store_transcript")


def downgrade() -> None:
    # Re-add store_audio and store_transcript to realtime_sessions
    op.add_column(
        "realtime_sessions",
        sa.Column("store_audio", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "realtime_sessions",
        sa.Column(
            "store_transcript", sa.Boolean(), nullable=False, server_default="false"
        ),
    )

    # Restore store flags from retention value
    op.execute(
        """
        UPDATE realtime_sessions
        SET store_audio = (retention != 'none'),
            store_transcript = (retention != 'none')
        """
    )

    # Drop retention columns
    op.drop_column("realtime_sessions", "retention")
    op.drop_column("jobs", "retention")
