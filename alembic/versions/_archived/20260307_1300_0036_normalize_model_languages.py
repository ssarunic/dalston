"""Normalize models.languages JSON array to model_languages junction table.

Creates model_languages(model_id, language_code) and migrates data from the
existing languages JSON column. The languages column is kept nullable for
backward compatibility and will be dropped in a future migration.

Revision ID: 0036
Revises: 0035
Create Date: 2026-03-07 13:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_languages",
        sa.Column(
            "model_id",
            sa.String(200),
            sa.ForeignKey("models.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("language_code", sa.String(10), primary_key=True),
    )

    dialect = op.get_bind().dialect.name

    if dialect == "postgresql":
        # Unpack JSON array of language strings into junction rows
        op.execute(
            """
            INSERT INTO model_languages (model_id, language_code)
            SELECT id, json_array_elements_text(languages::json)
            FROM models
            WHERE languages IS NOT NULL
              AND languages != 'null'
              AND json_array_length(languages::json) > 0
            ON CONFLICT DO NOTHING
            """
        )
    else:
        # SQLite: use json_each to expand JSON array
        op.execute(
            """
            INSERT OR IGNORE INTO model_languages (model_id, language_code)
            SELECT m.id, je.value
            FROM models m, json_each(m.languages) AS je
            WHERE m.languages IS NOT NULL
            """
        )


def downgrade() -> None:
    op.drop_table("model_languages")
