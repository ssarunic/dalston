"""Add result stats fields to jobs and rename utterance_count to segment_count.

Revision ID: 0010
Revises: 20260213_2210_729dffbba68f
Create Date: 2026-02-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: Union[str, None] = "729dffbba68f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add result stats fields to jobs table
    op.add_column(
        "jobs",
        sa.Column("result_language_code", sa.String(10), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column("result_word_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column("result_segment_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column("result_speaker_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column("result_character_count", sa.Integer(), nullable=True),
    )

    # Rename utterance_count to segment_count in realtime_sessions
    op.alter_column(
        "realtime_sessions",
        "utterance_count",
        new_column_name="segment_count",
    )


def downgrade() -> None:
    # Rename segment_count back to utterance_count
    op.alter_column(
        "realtime_sessions",
        "segment_count",
        new_column_name="utterance_count",
    )

    # Drop result stats fields from jobs table
    op.drop_column("jobs", "result_character_count")
    op.drop_column("jobs", "result_speaker_count")
    op.drop_column("jobs", "result_segment_count")
    op.drop_column("jobs", "result_word_count")
    op.drop_column("jobs", "result_language_code")
