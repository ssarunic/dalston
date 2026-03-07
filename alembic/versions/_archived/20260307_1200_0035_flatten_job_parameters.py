"""Flatten jobs.parameters JSON into typed param_* columns.

Adds 15 typed columns to the jobs table mirroring the most-used keys from the
parameters JSON blob. The parameters column is kept as nullable for backward
compatibility and will be dropped in a future migration once confirmed empty.

Revision ID: 0035
Revises: 0034
Create Date: 2026-03-07 12:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

# Typed columns to add and their SQLAlchemy column types
_PARAM_COLUMNS: list[tuple[str, sa.types.TypeEngine]] = [
    ("param_language", sa.String(10)),
    ("param_model", sa.String(200)),
    ("param_word_timestamps", sa.Boolean()),
    ("param_timestamps_granularity", sa.String(20)),
    ("param_speaker_detection", sa.String(20)),
    ("param_num_speakers", sa.Integer()),
    ("param_min_speakers", sa.Integer()),
    ("param_max_speakers", sa.Integer()),
    ("param_beam_size", sa.Integer()),
    ("param_vad_filter", sa.Boolean()),
    ("param_exclusive", sa.Boolean()),
    ("param_num_channels", sa.Integer()),
    ("param_pii_confidence_threshold", sa.Float()),
    ("param_pii_buffer_ms", sa.Integer()),
    ("param_transcribe_config", sa.Text()),  # JSON blob stored as Text
]

# Mapping from column name (without param_ prefix) to JSON key
_JSON_KEY_MAP = {col.removeprefix("param_"): col for col, _ in _PARAM_COLUMNS}


def upgrade() -> None:
    dialect = op.get_bind().dialect.name

    for col_name, col_type in _PARAM_COLUMNS:
        op.add_column("jobs", sa.Column(col_name, col_type, nullable=True))

    # Data migration: extract values from parameters JSON into typed columns.
    # Postgres: use JSON operators. SQLite: use json_extract().
    if dialect == "postgresql":
        for json_key, col_name in _JSON_KEY_MAP.items():
            op.execute(
                f"""
                UPDATE jobs
                SET {col_name} = (parameters->>'{json_key}')
                WHERE parameters IS NOT NULL
                  AND parameters ? '{json_key}'
                  AND (parameters->>'{json_key}') IS NOT NULL
                """
            )
    else:
        # SQLite
        for json_key, col_name in _JSON_KEY_MAP.items():
            op.execute(
                f"""
                UPDATE jobs
                SET {col_name} = json_extract(parameters, '$.{json_key}')
                WHERE parameters IS NOT NULL
                """
            )

    # Make parameters column nullable (was NOT NULL DEFAULT '{}')
    if dialect == "postgresql":
        op.alter_column("jobs", "parameters", nullable=True)
    # SQLite: column is already nullable in the hand-rolled lite schema


def downgrade() -> None:
    for col_name, _ in reversed(_PARAM_COLUMNS):
        op.drop_column("jobs", col_name)

    # Restore parameters NOT NULL constraint on Postgres
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute("UPDATE jobs SET parameters = '{}' WHERE parameters IS NULL")
        op.alter_column("jobs", "parameters", nullable=False)
