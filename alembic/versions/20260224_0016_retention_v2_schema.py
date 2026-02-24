"""Create retention V2 schema: artifact_objects, retention_templates, retention_template_rules.

Revision ID: 0016
Revises: 0015
Create Date: 2026-02-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Create retention_templates table
    op.create_table(
        "retention_templates",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,  # NULL for system templates
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "is_system",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
    )

    # Unique constraint: (tenant_id, name) with NULLS NOT DISTINCT
    op.create_index(
        "ix_retention_templates_tenant_name",
        "retention_templates",
        ["tenant_id", "name"],
        unique=True,
        postgresql_nulls_not_distinct=True,
    )

    op.create_index(
        "ix_retention_templates_tenant_id",
        "retention_templates",
        ["tenant_id"],
    )

    # Create retention_template_rules table
    op.create_table(
        "retention_template_rules",
        sa.Column(
            "template_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("artifact_type", sa.String(50), nullable=False),
        sa.Column("store", sa.Boolean(), nullable=False),
        sa.Column("ttl_seconds", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("template_id", "artifact_type"),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["retention_templates.id"],
            ondelete="CASCADE",
        ),
    )

    # Create artifact_objects table
    op.create_table(
        "artifact_objects",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("owner_type", sa.String(20), nullable=False),  # job | session
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "artifact_type", sa.String(50), nullable=False
        ),  # audio.source, transcript.redacted, etc.
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column(
            "sensitivity", sa.String(20), nullable=False
        ),  # raw_pii | redacted | metadata
        sa.Column(
            "compliance_tags",
            postgresql.ARRAY(sa.String()),
            nullable=True,
        ),  # gdpr, hipaa, pci, pii-processed
        sa.Column("store", sa.Boolean(), nullable=False),
        sa.Column("ttl_seconds", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "available_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("purge_after", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("purged_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.UniqueConstraint(
            "owner_type",
            "owner_id",
            "artifact_type",
            "uri",
            name="uq_artifact_objects_owner_type_uri",
        ),
    )

    # Index for purge worker: find expired artifacts that need purging
    op.create_index(
        "ix_artifact_objects_purge",
        "artifact_objects",
        ["purge_after"],
        postgresql_where=sa.text("purge_after IS NOT NULL AND purged_at IS NULL"),
    )

    # Index for listing artifacts by owner
    op.create_index(
        "ix_artifact_objects_owner",
        "artifact_objects",
        ["owner_type", "owner_id"],
    )

    # Index for tenant-based queries
    op.create_index(
        "ix_artifact_objects_tenant_id",
        "artifact_objects",
        ["tenant_id"],
    )

    # Add V2 retention columns to jobs table
    op.add_column(
        "jobs",
        sa.Column("retention_snapshot", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "retention_template_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_jobs_retention_template_id",
        "jobs",
        "retention_templates",
        ["retention_template_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Add V2 retention columns to realtime_sessions table
    op.add_column(
        "realtime_sessions",
        sa.Column("retention_snapshot", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "realtime_sessions",
        sa.Column(
            "retention_template_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_realtime_sessions_retention_template_id",
        "realtime_sessions",
        "retention_templates",
        ["retention_template_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Seed system templates
    _seed_system_templates()


def _seed_system_templates() -> None:
    """Seed system retention templates."""
    # System template IDs (well-known UUIDs)
    TEMPLATE_DEFAULT = "00000000-0000-0000-0000-000000000010"
    TEMPLATE_ZERO = "00000000-0000-0000-0000-000000000011"
    TEMPLATE_KEEP = "00000000-0000-0000-0000-000000000012"
    TEMPLATE_PII = "00000000-0000-0000-0000-000000000013"

    # Insert system templates
    op.execute(
        f"""
        INSERT INTO retention_templates (id, tenant_id, name, description, is_system)
        VALUES
            ('{TEMPLATE_DEFAULT}', NULL, 'default',
             'Standard retention: 24h audio, keep transcript forever', true),
            ('{TEMPLATE_ZERO}', NULL, 'zero-retention',
             'Immediate purge of all artifacts after processing', true),
            ('{TEMPLATE_KEEP}', NULL, 'keep-forever',
             'Keep all artifacts indefinitely', true),
            ('{TEMPLATE_PII}', NULL, 'pii-compliant',
             'PII-safe defaults: delete original immediately, keep redacted', true)
        """
    )

    # Insert template rules for 'default' template
    op.execute(
        f"""
        INSERT INTO retention_template_rules (template_id, artifact_type, store, ttl_seconds)
        VALUES
            ('{TEMPLATE_DEFAULT}', 'audio.source', true, 86400),
            ('{TEMPLATE_DEFAULT}', 'audio.redacted', true, NULL),
            ('{TEMPLATE_DEFAULT}', 'transcript.raw', true, NULL),
            ('{TEMPLATE_DEFAULT}', 'transcript.redacted', true, NULL),
            ('{TEMPLATE_DEFAULT}', 'pii.entities', true, NULL),
            ('{TEMPLATE_DEFAULT}', 'pipeline.intermediate', false, NULL),
            ('{TEMPLATE_DEFAULT}', 'realtime.transcript', true, 86400),
            ('{TEMPLATE_DEFAULT}', 'realtime.events', false, NULL)
        """
    )

    # Insert template rules for 'zero-retention' template
    op.execute(
        f"""
        INSERT INTO retention_template_rules (template_id, artifact_type, store, ttl_seconds)
        VALUES
            ('{TEMPLATE_ZERO}', 'audio.source', true, 0),
            ('{TEMPLATE_ZERO}', 'audio.redacted', true, 0),
            ('{TEMPLATE_ZERO}', 'transcript.raw', true, 0),
            ('{TEMPLATE_ZERO}', 'transcript.redacted', true, 0),
            ('{TEMPLATE_ZERO}', 'pii.entities', true, 0),
            ('{TEMPLATE_ZERO}', 'pipeline.intermediate', false, NULL),
            ('{TEMPLATE_ZERO}', 'realtime.transcript', true, 0),
            ('{TEMPLATE_ZERO}', 'realtime.events', false, NULL)
        """
    )

    # Insert template rules for 'keep-forever' template
    op.execute(
        f"""
        INSERT INTO retention_template_rules (template_id, artifact_type, store, ttl_seconds)
        VALUES
            ('{TEMPLATE_KEEP}', 'audio.source', true, NULL),
            ('{TEMPLATE_KEEP}', 'audio.redacted', true, NULL),
            ('{TEMPLATE_KEEP}', 'transcript.raw', true, NULL),
            ('{TEMPLATE_KEEP}', 'transcript.redacted', true, NULL),
            ('{TEMPLATE_KEEP}', 'pii.entities', true, NULL),
            ('{TEMPLATE_KEEP}', 'pipeline.intermediate', false, NULL),
            ('{TEMPLATE_KEEP}', 'realtime.transcript', true, NULL),
            ('{TEMPLATE_KEEP}', 'realtime.events', false, NULL)
        """
    )

    # Insert template rules for 'pii-compliant' template
    # - audio.source: ttl_seconds=0 (delete immediately after redaction)
    # - audio.redacted: keep forever
    # - transcript.raw: store=false (never persist raw PII)
    # - transcript.redacted: keep forever
    # - pii.entities: 90 days (audit trail)
    op.execute(
        f"""
        INSERT INTO retention_template_rules (template_id, artifact_type, store, ttl_seconds)
        VALUES
            ('{TEMPLATE_PII}', 'audio.source', true, 0),
            ('{TEMPLATE_PII}', 'audio.redacted', true, NULL),
            ('{TEMPLATE_PII}', 'transcript.raw', false, NULL),
            ('{TEMPLATE_PII}', 'transcript.redacted', true, NULL),
            ('{TEMPLATE_PII}', 'pii.entities', true, 7776000),
            ('{TEMPLATE_PII}', 'pipeline.intermediate', false, NULL),
            ('{TEMPLATE_PII}', 'realtime.transcript', false, NULL),
            ('{TEMPLATE_PII}', 'realtime.events', false, NULL)
        """
    )


def downgrade() -> None:
    # Drop foreign keys first
    op.drop_constraint(
        "fk_realtime_sessions_retention_template_id",
        "realtime_sessions",
        type_="foreignkey",
    )
    op.drop_constraint("fk_jobs_retention_template_id", "jobs", type_="foreignkey")

    # Drop V2 columns from jobs and realtime_sessions
    op.drop_column("realtime_sessions", "retention_template_id")
    op.drop_column("realtime_sessions", "retention_snapshot")
    op.drop_column("jobs", "retention_template_id")
    op.drop_column("jobs", "retention_snapshot")

    # Drop artifact_objects table
    op.drop_index("ix_artifact_objects_tenant_id", table_name="artifact_objects")
    op.drop_index("ix_artifact_objects_owner", table_name="artifact_objects")
    op.drop_index("ix_artifact_objects_purge", table_name="artifact_objects")
    op.drop_table("artifact_objects")

    # Drop retention_template_rules table
    op.drop_table("retention_template_rules")

    # Drop retention_templates table
    op.drop_index("ix_retention_templates_tenant_id", table_name="retention_templates")
    op.drop_index(
        "ix_retention_templates_tenant_name", table_name="retention_templates"
    )
    op.drop_table("retention_templates")
