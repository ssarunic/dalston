"""Bridge migration for pre-M57.0 SQLite lite databases.

Fresh SQLite databases are bootstrapped directly by migrate.upgrade_to_head()
using Base.metadata.create_all(), bypassing the historical migration chain.
This migration handles EXISTING pre-M57.0 lite databases that need to be
brought to parity with the M57.0 schema.

For Postgres: no-op (the full migration chain handles everything).
For SQLite:
  - Adds the four junction tables if not already present
  - Adds param_* columns to jobs table if not already present
  - Adds model_languages table if not already present
  - Migrates data from legacy columns/JSON into the new structures
  - All operations are idempotent (IF NOT EXISTS / INSERT OR IGNORE)

Revision ID: 0038
Revises: 0037
Create Date: 2026-03-07 15:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name

    if dialect != "sqlite":
        # Postgres uses the full migration chain; this is a no-op there.
        return

    # -------------------------------------------------------------------
    # Ensure junction tables exist (idempotent for fresh DBs stamped at 0038)
    # -------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS task_dependencies (
            task_id TEXT NOT NULL,
            depends_on_id TEXT NOT NULL,
            PRIMARY KEY (task_id, depends_on_id),
            FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE,
            FOREIGN KEY(depends_on_id) REFERENCES tasks(id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS webhook_endpoint_events (
            endpoint_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            PRIMARY KEY (endpoint_id, event_type),
            FOREIGN KEY(endpoint_id) REFERENCES webhook_endpoints(id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS job_pii_entity_types (
            job_id TEXT NOT NULL,
            entity_type_id TEXT NOT NULL,
            PRIMARY KEY (job_id, entity_type_id),
            FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS artifact_compliance_tags (
            artifact_id TEXT NOT NULL,
            tag TEXT NOT NULL,
            PRIMARY KEY (artifact_id, tag),
            FOREIGN KEY(artifact_id) REFERENCES artifact_objects(id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS model_languages (
            model_id TEXT NOT NULL,
            language_code TEXT NOT NULL,
            PRIMARY KEY (model_id, language_code),
            FOREIGN KEY(model_id) REFERENCES models(id) ON DELETE CASCADE
        )
        """
    )

    # -------------------------------------------------------------------
    # Migrate model languages from JSON column → model_languages rows
    # -------------------------------------------------------------------
    op.execute(
        """
        INSERT OR IGNORE INTO model_languages (model_id, language_code)
        SELECT m.id, je.value
        FROM models m, json_each(m.languages) AS je
        WHERE m.languages IS NOT NULL
        """
    )

    # -------------------------------------------------------------------
    # Add param_* columns to jobs (all nullable; idempotent via PRAGMA check)
    # -------------------------------------------------------------------
    _param_columns = [
        ("param_language", "TEXT"),
        ("param_model", "TEXT"),
        ("param_word_timestamps", "INTEGER"),  # BOOLEAN stored as INTEGER in SQLite
        ("param_timestamps_granularity", "TEXT"),
        ("param_speaker_detection", "TEXT"),
        ("param_num_speakers", "INTEGER"),
        ("param_min_speakers", "INTEGER"),
        ("param_max_speakers", "INTEGER"),
        ("param_beam_size", "INTEGER"),
        ("param_vad_filter", "INTEGER"),
        ("param_exclusive", "INTEGER"),
        ("param_num_channels", "INTEGER"),
        ("param_pii_confidence_threshold", "REAL"),
        ("param_pii_buffer_ms", "INTEGER"),
        ("param_transcribe_config", "TEXT"),
    ]

    conn = op.get_bind()
    result = conn.execute(sa.text('PRAGMA table_info("jobs")'))
    existing_cols = {row[1] for row in result.fetchall()}

    for col_name, col_type in _param_columns:
        if col_name not in existing_cols:
            op.execute(
                sa.text(f'ALTER TABLE "jobs" ADD COLUMN "{col_name}" {col_type}')
            )

    # -------------------------------------------------------------------
    # Backfill param_* columns from parameters JSON
    # -------------------------------------------------------------------
    _json_to_col = {
        "language": "param_language",
        "model": "param_model",
        "word_timestamps": "param_word_timestamps",
        "timestamps_granularity": "param_timestamps_granularity",
        "speaker_detection": "param_speaker_detection",
        "num_speakers": "param_num_speakers",
        "min_speakers": "param_min_speakers",
        "max_speakers": "param_max_speakers",
        "beam_size": "param_beam_size",
        "vad_filter": "param_vad_filter",
        "exclusive": "param_exclusive",
        "num_channels": "param_num_channels",
        "pii_confidence_threshold": "param_pii_confidence_threshold",
        "pii_buffer_ms": "param_pii_buffer_ms",
        "transcribe_config": "param_transcribe_config",
    }

    for json_key, col_name in _json_to_col.items():
        op.execute(
            sa.text(
                f"""
                UPDATE jobs
                SET {col_name} = json_extract(parameters, '$.{json_key}')
                WHERE parameters IS NOT NULL
                  AND {col_name} IS NULL
                """
            )
        )


def downgrade() -> None:
    dialect = op.get_bind().dialect.name

    if dialect != "sqlite":
        return

    op.drop_table("model_languages")
    op.drop_table("artifact_compliance_tags")
    op.drop_table("job_pii_entity_types")
    op.drop_table("webhook_endpoint_events")
    op.drop_table("task_dependencies")
    # param_* columns cannot be dropped in SQLite without table recreation
    # (handled by alembic batch mode if needed in a future downgrade migration)
