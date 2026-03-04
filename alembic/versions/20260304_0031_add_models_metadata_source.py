"""Add metadata_source column to models table.

Revision ID: 0031
Revises: 0030
Create Date: 2026-03-04

Tracks the provenance of model metadata:
- "yaml": Populated from YAML files (can be updated on re-seed)
- "user": Manually enriched (preserved across re-seeds)
- "hf": Auto-resolved from HuggingFace (can be enriched by user)
"""

import sqlalchemy as sa

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "models",
        sa.Column(
            "metadata_source",
            sa.String(20),
            nullable=False,
            server_default="yaml",
        ),
    )


def downgrade() -> None:
    op.drop_column("models", "metadata_source")
