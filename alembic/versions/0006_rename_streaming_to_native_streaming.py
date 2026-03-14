"""Rename streaming column to native_streaming.

All models are now realtime-eligible via VAD-chunked inference.
native_streaming indicates cache-aware incremental decode (RNNT/TDT).

Revision ID: 0006_rename_streaming_to_native_streaming
Revises: 0005_add_model_download_progress_columns
Create Date: 2026-03-13
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_rename_streaming_to_native_streaming"
down_revision: str = "0005_add_model_download_progress_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("models", "streaming", new_column_name="native_streaming")


def downgrade() -> None:
    op.alter_column("models", "native_streaming", new_column_name="streaming")
