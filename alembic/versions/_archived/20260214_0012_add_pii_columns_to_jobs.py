"""Add PII detection columns to jobs table.

Revision ID: 0012
Revises: 0011
Create Date: 2026-02-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# Revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add PII detection columns to jobs table
    op.add_column(
        "jobs",
        sa.Column(
            "pii_detection_enabled",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "pii_detection_tier",
            sa.String(20),
            nullable=True,
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "pii_entity_types",
            postgresql.ARRAY(sa.String()),
            nullable=True,
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "pii_redact_audio",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "pii_redaction_mode",
            sa.String(20),
            nullable=True,
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "pii_entities_detected",
            sa.Integer(),
            nullable=True,
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "pii_redacted_audio_uri",
            sa.Text(),
            nullable=True,
        ),
    )

    # Index for querying jobs with PII detection enabled
    op.create_index(
        "ix_jobs_pii_detection_enabled",
        "jobs",
        ["pii_detection_enabled"],
        postgresql_where=sa.text("pii_detection_enabled = true"),
    )


def downgrade() -> None:
    op.drop_index("ix_jobs_pii_detection_enabled", table_name="jobs")
    op.drop_column("jobs", "pii_redacted_audio_uri")
    op.drop_column("jobs", "pii_entities_detected")
    op.drop_column("jobs", "pii_redaction_mode")
    op.drop_column("jobs", "pii_redact_audio")
    op.drop_column("jobs", "pii_entity_types")
    op.drop_column("jobs", "pii_detection_tier")
    op.drop_column("jobs", "pii_detection_enabled")
