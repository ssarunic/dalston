"""Create models table for model registry.

Revision ID: 0024
Revises: 0023
Create Date: 2026-03-02

This migration creates the `models` table for M40 Model Registry.
The table tracks available ML models, their download status, capabilities,
and hardware requirements.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "models",
        # Identity - Dalston model ID (e.g., "parakeet-tdt-1.1b")
        sa.Column("id", sa.String(100), primary_key=True),
        sa.Column("name", sa.String(200), nullable=True),
        # Runtime mapping
        sa.Column("runtime", sa.String(50), nullable=False),  # nemo, faster-whisper
        sa.Column(
            "runtime_model_id", sa.String(200), nullable=False
        ),  # HF model ID or path
        sa.Column("stage", sa.String(50), nullable=False),  # transcribe, diarize, align
        # Download status
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="not_downloaded",
        ),
        sa.Column("download_path", sa.Text, nullable=True),
        sa.Column("size_bytes", sa.BigInteger, nullable=True),
        sa.Column("downloaded_at", sa.TIMESTAMP(timezone=True), nullable=True),
        # Source and library info
        sa.Column("source", sa.String(50), nullable=True),  # huggingface, local
        sa.Column("library_name", sa.String(50), nullable=True),  # ctranslate2, nemo
        sa.Column("languages", JSONB, nullable=True),  # ["en", "es", "fr"]
        # Capabilities
        sa.Column("word_timestamps", sa.Boolean, server_default="false"),
        sa.Column("punctuation", sa.Boolean, server_default="false"),
        sa.Column("streaming", sa.Boolean, server_default="false"),
        # Hardware requirements
        sa.Column("min_vram_gb", sa.Float, nullable=True),
        sa.Column("min_ram_gb", sa.Float, nullable=True),
        sa.Column("supports_cpu", sa.Boolean, server_default="true"),
        # Metadata cache (for HuggingFace card data, etc.)
        # Named model_metadata to avoid SQLAlchemy reserved attribute conflict
        sa.Column("model_metadata", JSONB, nullable=False, server_default="{}"),
        # Usage tracking
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        # Timestamps
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # Indexes for common queries
    op.create_index("ix_models_runtime", "models", ["runtime"])
    op.create_index("ix_models_stage", "models", ["stage"])
    op.create_index("ix_models_status", "models", ["status"])


def downgrade() -> None:
    op.drop_index("ix_models_status", table_name="models")
    op.drop_index("ix_models_stage", table_name="models")
    op.drop_index("ix_models_runtime", table_name="models")
    op.drop_table("models")
