"""Rename runtime identity columns to engine_id/loaded_model_id.

Revision ID: 0004_rename_runtime_identity_columns
Revises: 0003_backfill_model_transcribe
Create Date: 2026-03-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_rename_runtime_identity_columns"
down_revision: str | None = "0003_backfill_model_transcribe"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {col["name"] for col in inspector.get_columns(table_name)}
    return column_name in columns


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    indexes = {idx["name"] for idx in inspector.get_indexes(table_name)}
    return index_name in indexes


def upgrade() -> None:
    if _has_column("tasks", "runtime"):
        with op.batch_alter_table("tasks") as batch_op:
            batch_op.alter_column(
                "runtime",
                new_column_name="engine_id",
                existing_type=sa.String(length=100),
            )

    if _has_column("realtime_sessions", "runtime"):
        with op.batch_alter_table("realtime_sessions") as batch_op:
            batch_op.alter_column(
                "runtime",
                new_column_name="engine_id",
                existing_type=sa.String(length=50),
            )

    if _has_column("models", "runtime"):
        with op.batch_alter_table("models") as batch_op:
            batch_op.alter_column(
                "runtime",
                new_column_name="engine_id",
                existing_type=sa.String(length=50),
            )

    if _has_column("models", "runtime_model_id"):
        with op.batch_alter_table("models") as batch_op:
            batch_op.alter_column(
                "runtime_model_id",
                new_column_name="loaded_model_id",
                existing_type=sa.String(length=200),
            )

    has_old_index = _has_index("models", "ix_models_runtime")
    has_new_index = _has_index("models", "ix_models_engine_id")

    if has_old_index:
        with op.batch_alter_table("models") as batch_op:
            batch_op.drop_index("ix_models_runtime")

    if not has_new_index:
        with op.batch_alter_table("models") as batch_op:
            batch_op.create_index("ix_models_engine_id", ["engine_id"], unique=False)


def downgrade() -> None:
    if _has_column("models", "loaded_model_id"):
        with op.batch_alter_table("models") as batch_op:
            batch_op.alter_column(
                "loaded_model_id",
                new_column_name="runtime_model_id",
                existing_type=sa.String(length=200),
            )

    if _has_column("models", "engine_id"):
        with op.batch_alter_table("models") as batch_op:
            batch_op.alter_column(
                "engine_id",
                new_column_name="runtime",
                existing_type=sa.String(length=50),
            )

    if _has_column("realtime_sessions", "engine_id"):
        with op.batch_alter_table("realtime_sessions") as batch_op:
            batch_op.alter_column(
                "engine_id",
                new_column_name="runtime",
                existing_type=sa.String(length=50),
            )

    if _has_column("tasks", "engine_id"):
        with op.batch_alter_table("tasks") as batch_op:
            batch_op.alter_column(
                "engine_id",
                new_column_name="runtime",
                existing_type=sa.String(length=100),
            )

    has_new_index = _has_index("models", "ix_models_engine_id")
    has_old_index = _has_index("models", "ix_models_runtime")

    if has_new_index:
        with op.batch_alter_table("models") as batch_op:
            batch_op.drop_index("ix_models_engine_id")

    if not has_old_index:
        with op.batch_alter_table("models") as batch_op:
            batch_op.create_index("ix_models_runtime", ["runtime"], unique=False)
