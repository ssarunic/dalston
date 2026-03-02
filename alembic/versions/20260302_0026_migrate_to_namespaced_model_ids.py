"""Migrate model IDs to namespaced format.

Revision ID: 0026
Revises: 0025
Create Date: 2026-03-02

This migration updates model IDs from short format (e.g., "parakeet-tdt-1.1b")
to full namespaced format (e.g., "nvidia/parakeet-tdt-1.1b") for consistency
with HuggingFace model identifiers.

Also extends the id column from String(100) to String(200) to accommodate
longer namespaced IDs.
"""

import sqlalchemy as sa

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

# Mapping from old short IDs to new namespaced IDs
MODEL_ID_MAPPING = {
    "parakeet-tdt-1.1b": "nvidia/parakeet-tdt-1.1b",
    "parakeet-tdt-0.6b-v3": "nvidia/parakeet-tdt-0.6b-v3",
    "parakeet-ctc-0.6b": "nvidia/parakeet-ctc-0.6b",
    "parakeet-ctc-1.1b": "nvidia/parakeet-ctc-1.1b",
    "faster-whisper-large-v3-turbo": "Systran/faster-whisper-large-v3-turbo",
    "faster-whisper-large-v3": "Systran/faster-whisper-large-v3",
    "faster-whisper-medium": "Systran/faster-whisper-medium",
    "faster-whisper-small": "Systran/faster-whisper-small",
    "faster-whisper-base": "Systran/faster-whisper-base",
    "faster-whisper-tiny": "Systran/faster-whisper-tiny",
}


def upgrade() -> None:
    # First, extend the id column to accommodate longer namespaced IDs
    op.alter_column(
        "models",
        "id",
        type_=sa.String(200),
        existing_type=sa.String(100),
        existing_nullable=False,
    )

    # Update existing model IDs to namespaced format
    for old_id, new_id in MODEL_ID_MAPPING.items():
        op.execute(
            sa.text(
                "UPDATE models SET id = :new_id WHERE id = :old_id"
            ).bindparams(old_id=old_id, new_id=new_id)
        )


def downgrade() -> None:
    # Revert model IDs back to short format
    for old_id, new_id in MODEL_ID_MAPPING.items():
        op.execute(
            sa.text(
                "UPDATE models SET id = :old_id WHERE id = :new_id"
            ).bindparams(old_id=old_id, new_id=new_id)
        )

    # Shrink id column back (may fail if data exceeds 100 chars)
    op.alter_column(
        "models",
        "id",
        type_=sa.String(100),
        existing_type=sa.String(200),
        existing_nullable=False,
    )
