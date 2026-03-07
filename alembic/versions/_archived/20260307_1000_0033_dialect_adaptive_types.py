"""Dialect-adaptive types: remove server-level UUID and JSON defaults.

Revision ID: 0033
Revises: 0032
Create Date: 2026-03-07

Switch from server-level defaults (gen_random_uuid(), '{}') to Python-level
defaults (uuid4, dict). This makes the schema compatible with both Postgres
and SQLite:

- UUID PKs: server_default=gen_random_uuid() -> default=uuid4 (Python level)
  SQLite has no gen_random_uuid() function; Python-level default works on both.

- JSON columns: server_default='{}' -> default=dict (Python level)
  SQLite stores JSON as TEXT; the string '{}' is valid but Python-level
  serialization via JSONType is cleaner and avoids dialect-specific DDL.

The ORM (via UUIDType/JSONType TypeDecorators) now provides these values
before the INSERT, so no server-level default is required. Existing rows
are unaffected.
"""

import sqlalchemy as sa

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None

# UUID PK columns that had server_default=gen_random_uuid()
_UUID_PK_COLUMNS = [
    ("tenants", "id"),
    ("jobs", "id"),
    ("tasks", "id"),
    ("api_keys", "id"),
    ("webhook_endpoints", "id"),
    ("webhook_deliveries", "id"),
    ("artifact_objects", "id"),
    ("settings", "id"),
]

# JSON columns that had server_default='{}'
_JSON_COLUMNS_WITH_SERVER_DEFAULT = [
    ("tenants", "settings"),
    ("jobs", "parameters"),
    ("tasks", "config"),
    ("models", "model_metadata"),
]


def upgrade() -> None:
    # Remove server-level UUID defaults — Python-level uuid4 is used instead.
    # existing_type must be specified so Alembic can render the ALTER correctly.
    for table, col in _UUID_PK_COLUMNS:
        op.alter_column(
            table,
            col,
            server_default=None,
            existing_type=sa.dialects.postgresql.UUID(as_uuid=True)
            if op.get_bind().dialect.name == "postgresql"
            else sa.String(36),
        )

    # Remove server-level JSON defaults — Python-level dict is used instead.
    for table, col in _JSON_COLUMNS_WITH_SERVER_DEFAULT:
        op.alter_column(
            table,
            col,
            server_default=None,
            existing_type=sa.dialects.postgresql.JSONB()
            if op.get_bind().dialect.name == "postgresql"
            else sa.Text(),
            existing_nullable=False,
        )


def downgrade() -> None:
    # Restore Postgres-level server defaults (no-op on SQLite).
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    for table, col in _JSON_COLUMNS_WITH_SERVER_DEFAULT:
        op.alter_column(
            table,
            col,
            server_default="{}",
            existing_type=sa.dialects.postgresql.JSONB(),
            existing_nullable=False,
        )

    for table, col in _UUID_PK_COLUMNS:
        op.alter_column(
            table,
            col,
            server_default=sa.text("gen_random_uuid()"),
            existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
        )
