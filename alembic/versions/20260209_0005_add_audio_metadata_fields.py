"""Add audio metadata fields to jobs table.

Stores audio file properties (format, duration, sample_rate, channels, bit_depth)
extracted at upload time for validation and pipeline configuration.

Revision ID: 0005
Revises: 0004
Create Date: 2026-02-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# Revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("audio_format", sa.String(20), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column("audio_duration", sa.Float(), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column("audio_sample_rate", sa.Integer(), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column("audio_channels", sa.Integer(), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column("audio_bit_depth", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("jobs", "audio_bit_depth")
    op.drop_column("jobs", "audio_channels")
    op.drop_column("jobs", "audio_sample_rate")
    op.drop_column("jobs", "audio_duration")
    op.drop_column("jobs", "audio_format")
