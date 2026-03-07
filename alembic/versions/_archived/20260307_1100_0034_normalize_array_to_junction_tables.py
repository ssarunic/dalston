"""Normalize ARRAY columns to junction tables.

Replaces four Postgres-specific ARRAY columns with portable junction tables:
  - tasks.dependencies        -> task_dependencies(task_id, depends_on_id)
  - webhook_endpoints.events  -> webhook_endpoint_events(endpoint_id, event_type)
  - jobs.pii_entity_types     -> job_pii_entity_types(job_id, entity_type_id)
  - artifact_objects.compliance_tags -> artifact_compliance_tags(artifact_id, tag)

Data is migrated out of the ARRAY columns before they are dropped.

Revision ID: 0034
Revises: 0033
Create Date: 2026-03-07 11:00:00
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name

    # ------------------------------------------------------------------
    # task_dependencies
    # ------------------------------------------------------------------
    op.create_table(
        "task_dependencies",
        sa.Column("task_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("depends_on_id", sa.String(36), sa.ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True),
    )

    if dialect == "postgresql":
        op.execute(
            """
            INSERT INTO task_dependencies (task_id, depends_on_id)
            SELECT id::text, unnest(dependencies)::text
            FROM tasks
            WHERE dependencies IS NOT NULL AND array_length(dependencies, 1) > 0
            """
        )
        op.drop_column("tasks", "dependencies")
    # SQLite: column never existed in SQLite schema (lite mode used session.py bootstrap
    # which never included dependencies column with ARRAY type); nothing to migrate.

    # ------------------------------------------------------------------
    # webhook_endpoint_events
    # ------------------------------------------------------------------
    op.create_table(
        "webhook_endpoint_events",
        sa.Column("endpoint_id", sa.String(36), sa.ForeignKey("webhook_endpoints.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("event_type", sa.String(50), primary_key=True),
    )

    if dialect == "postgresql":
        op.execute(
            """
            INSERT INTO webhook_endpoint_events (endpoint_id, event_type)
            SELECT id::text, unnest(events)
            FROM webhook_endpoints
            WHERE events IS NOT NULL AND array_length(events, 1) > 0
            """
        )
        op.drop_column("webhook_endpoints", "events")

    # ------------------------------------------------------------------
    # job_pii_entity_types
    # ------------------------------------------------------------------
    op.create_table(
        "job_pii_entity_types",
        sa.Column("job_id", sa.String(36), sa.ForeignKey("jobs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("entity_type_id", sa.String(50), primary_key=True),
    )

    if dialect == "postgresql":
        op.execute(
            """
            INSERT INTO job_pii_entity_types (job_id, entity_type_id)
            SELECT id::text, unnest(pii_entity_types)
            FROM jobs
            WHERE pii_entity_types IS NOT NULL AND array_length(pii_entity_types, 1) > 0
            """
        )
        op.drop_column("jobs", "pii_entity_types")

    # ------------------------------------------------------------------
    # artifact_compliance_tags
    # ------------------------------------------------------------------
    op.create_table(
        "artifact_compliance_tags",
        sa.Column("artifact_id", sa.String(36), sa.ForeignKey("artifact_objects.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tag", sa.String(50), primary_key=True),
    )

    if dialect == "postgresql":
        op.execute(
            """
            INSERT INTO artifact_compliance_tags (artifact_id, tag)
            SELECT id::text, unnest(compliance_tags)
            FROM artifact_objects
            WHERE compliance_tags IS NOT NULL AND array_length(compliance_tags, 1) > 0
            """
        )
        op.drop_column("artifact_objects", "compliance_tags")


def downgrade() -> None:
    dialect = op.get_bind().dialect.name

    # ------------------------------------------------------------------
    # artifact_compliance_tags -> artifact_objects.compliance_tags
    # ------------------------------------------------------------------
    op.drop_table("artifact_compliance_tags")
    if dialect == "postgresql":
        op.add_column(
            "artifact_objects",
            sa.Column("compliance_tags", sa.ARRAY(sa.String()), nullable=True),
        )

    # ------------------------------------------------------------------
    # job_pii_entity_types -> jobs.pii_entity_types
    # ------------------------------------------------------------------
    op.drop_table("job_pii_entity_types")
    if dialect == "postgresql":
        op.add_column(
            "jobs",
            sa.Column("pii_entity_types", sa.ARRAY(sa.String()), nullable=True),
        )

    # ------------------------------------------------------------------
    # webhook_endpoint_events -> webhook_endpoints.events
    # ------------------------------------------------------------------
    op.drop_table("webhook_endpoint_events")
    if dialect == "postgresql":
        op.add_column(
            "webhook_endpoints",
            sa.Column("events", sa.ARRAY(sa.String()), nullable=False, server_default="{}"),
        )

    # ------------------------------------------------------------------
    # task_dependencies -> tasks.dependencies
    # ------------------------------------------------------------------
    op.drop_table("task_dependencies")
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import UUID as PG_UUID

        op.add_column(
            "tasks",
            sa.Column(
                "dependencies",
                sa.ARRAY(PG_UUID(as_uuid=True)),
                nullable=False,
                server_default="{}",
            ),
        )
