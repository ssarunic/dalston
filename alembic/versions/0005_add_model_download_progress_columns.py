"""Add model download progress columns.

Revision ID: 0005_add_model_download_progress_columns
Revises: 0004_rename_runtime_identity_columns
Create Date: 2026-03-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_add_model_download_progress_columns"
down_revision: str | None = "0004_rename_runtime_identity_columns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns(table_name)}
    return column_name in columns


def upgrade() -> None:
    needs_expected = not _has_column("models", "expected_total_bytes")
    needs_downloaded = not _has_column("models", "downloaded_bytes")
    needs_progress_ts = not _has_column("models", "progress_updated_at")

    if needs_expected or needs_downloaded or needs_progress_ts:
        with op.batch_alter_table("models") as batch_op:
            if needs_expected:
                batch_op.add_column(sa.Column("expected_total_bytes", sa.BigInteger(), nullable=True))
            if needs_downloaded:
                batch_op.add_column(sa.Column("downloaded_bytes", sa.BigInteger(), nullable=True))
            if needs_progress_ts:
                batch_op.add_column(
                    sa.Column("progress_updated_at", sa.TIMESTAMP(timezone=True), nullable=True)
                )


def downgrade() -> None:
    has_progress_ts = _has_column("models", "progress_updated_at")
    has_downloaded = _has_column("models", "downloaded_bytes")
    has_expected = _has_column("models", "expected_total_bytes")

    if has_progress_ts or has_downloaded or has_expected:
        with op.batch_alter_table("models") as batch_op:
            if has_progress_ts:
                batch_op.drop_column("progress_updated_at")
            if has_downloaded:
                batch_op.drop_column("downloaded_bytes")
            if has_expected:
                batch_op.drop_column("expected_total_bytes")
