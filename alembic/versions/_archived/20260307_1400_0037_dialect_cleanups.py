"""Dialect-specific cleanup and documentation checkpoint.

This migration serves as a checkpoint confirming the schema is now fully
dialect-portable after M57.0 phases 1-5:
  - All UUID columns use UUIDType (no native PG_UUID server defaults)
  - All JSON columns use JSONType (no raw JSONB)
  - All ARRAY columns replaced by junction tables
  - Dialect-specific query patterns replaced with helpers

No DDL changes are made here; the migration exists to:
1. Provide a named checkpoint in the revision chain
2. Verify render_as_batch is active for SQLite (env.py handles this)

Revision ID: 0037
Revises: 0036
Create Date: 2026-03-07 14:00:00
"""

from __future__ import annotations

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No DDL changes — this is a checkpoint revision.
    pass


def downgrade() -> None:
    pass
