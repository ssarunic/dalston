"""Initial schema — dialect-portable (PostgreSQL + SQLite).

All previous migrations (0001–0038) have been archived and consolidated
into this single migration. It creates the full current schema from
scratch using only dialect-portable types.

Revision ID: 0001
Revises: None
Create Date: 2026-03-07
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from dalston.db.types import UUIDType

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # tenants
    # ------------------------------------------------------------------
    op.create_table(
        "tenants",
        sa.Column("id", UUIDType, nullable=False),
        sa.Column("name", sa.String(255), nullable=False, unique=True),
        sa.Column("settings", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )

    # ------------------------------------------------------------------
    # api_keys
    # ------------------------------------------------------------------
    op.create_table(
        "api_keys",
        sa.Column("id", UUIDType, nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("prefix", sa.String(10), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("tenant_id", UUIDType, nullable=False),
        sa.Column("scopes", sa.String(255), nullable=False),
        sa.Column("rate_limit", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_by_key_id", UUIDType, nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["created_by_key_id"], ["api_keys.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_api_keys_tenant_id", "api_keys", ["tenant_id"])
    op.create_index("ix_api_keys_created_by_key_id", "api_keys", ["created_by_key_id"])

    # ------------------------------------------------------------------
    # jobs
    # ------------------------------------------------------------------
    op.create_table(
        "jobs",
        sa.Column("id", UUIDType, nullable=False),
        sa.Column("tenant_id", UUIDType, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("display_name", sa.String(255), nullable=False, server_default=""),
        sa.Column("audio_uri", sa.Text(), nullable=False),
        sa.Column("audio_format", sa.String(20), nullable=True),
        sa.Column("audio_duration", sa.Float(), nullable=True),
        sa.Column("audio_sample_rate", sa.Integer(), nullable=True),
        sa.Column("audio_channels", sa.Integer(), nullable=True),
        sa.Column("audio_bit_depth", sa.Integer(), nullable=True),
        sa.Column("parameters", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("retention", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("purge_after", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("purged_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("result_language_code", sa.String(10), nullable=True),
        sa.Column("result_word_count", sa.Integer(), nullable=True),
        sa.Column("result_segment_count", sa.Integer(), nullable=True),
        sa.Column("result_speaker_count", sa.Integer(), nullable=True),
        sa.Column("result_character_count", sa.Integer(), nullable=True),
        sa.Column("pii_detection_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("pii_redact_audio", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("pii_redaction_mode", sa.String(20), nullable=True),
        sa.Column("pii_entities_detected", sa.Integer(), nullable=True),
        sa.Column("pii_redacted_audio_uri", sa.Text(), nullable=True),
        sa.Column("param_language", sa.String(10), nullable=True),
        sa.Column("param_model", sa.String(200), nullable=True),
        sa.Column("param_word_timestamps", sa.Boolean(), nullable=True),
        sa.Column("param_timestamps_granularity", sa.String(20), nullable=True),
        sa.Column("param_speaker_detection", sa.String(20), nullable=True),
        sa.Column("param_num_speakers", sa.Integer(), nullable=True),
        sa.Column("param_min_speakers", sa.Integer(), nullable=True),
        sa.Column("param_max_speakers", sa.Integer(), nullable=True),
        sa.Column("param_beam_size", sa.Integer(), nullable=True),
        sa.Column("param_vad_filter", sa.Boolean(), nullable=True),
        sa.Column("param_exclusive", sa.Boolean(), nullable=True),
        sa.Column("param_num_channels", sa.Integer(), nullable=True),
        sa.Column("param_pii_confidence_threshold", sa.Float(), nullable=True),
        sa.Column("param_pii_buffer_ms", sa.Integer(), nullable=True),
        sa.Column("param_transcribe_config", sa.JSON(), nullable=True),
        sa.Column("created_by_key_id", UUIDType, nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["created_by_key_id"], ["api_keys.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_jobs_tenant_id", "jobs", ["tenant_id"])
    op.create_index("ix_jobs_status", "jobs", ["status"])
    op.create_index("ix_jobs_created_at", "jobs", ["created_at"])
    op.create_index("ix_jobs_created_by_key_id", "jobs", ["created_by_key_id"])

    # ------------------------------------------------------------------
    # job_pii_entity_types (junction)
    # ------------------------------------------------------------------
    op.create_table(
        "job_pii_entity_types",
        sa.Column("job_id", UUIDType, nullable=False),
        sa.Column("entity_type_id", sa.String(50), nullable=False),
        sa.PrimaryKeyConstraint("job_id", "entity_type_id"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
    )

    # ------------------------------------------------------------------
    # tasks
    # ------------------------------------------------------------------
    op.create_table(
        "tasks",
        sa.Column("id", UUIDType, nullable=False),
        sa.Column("job_id", UUIDType, nullable=False),
        sa.Column("stage", sa.String(50), nullable=False),
        sa.Column("runtime", sa.String(100), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("config", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("input_uri", sa.Text(), nullable=True),
        sa.Column("output_uri", sa.Text(), nullable=True),
        sa.Column("retries", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("required", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("job_id", "stage", name="uq_tasks_job_id_stage"),
    )
    op.create_index("ix_tasks_job_id", "tasks", ["job_id"])
    op.create_index("ix_tasks_stage", "tasks", ["stage"])
    op.create_index("ix_tasks_status", "tasks", ["status"])

    # ------------------------------------------------------------------
    # task_dependencies (junction)
    # ------------------------------------------------------------------
    op.create_table(
        "task_dependencies",
        sa.Column("task_id", UUIDType, nullable=False),
        sa.Column("depends_on_id", UUIDType, nullable=False),
        sa.PrimaryKeyConstraint("task_id", "depends_on_id"),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["depends_on_id"], ["tasks.id"], ondelete="CASCADE"),
    )

    # ------------------------------------------------------------------
    # webhook_endpoints
    # ------------------------------------------------------------------
    op.create_table(
        "webhook_endpoints",
        sa.Column("id", UUIDType, nullable=False),
        sa.Column("tenant_id", UUIDType, nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("signing_secret", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("disabled_reason", sa.String(50), nullable=True),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_success_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_by_key_id", UUIDType, nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["created_by_key_id"], ["api_keys.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_webhook_endpoints_tenant_id", "webhook_endpoints", ["tenant_id"])
    op.create_index("ix_webhook_endpoints_created_by_key_id", "webhook_endpoints", ["created_by_key_id"])

    # ------------------------------------------------------------------
    # webhook_endpoint_events (junction)
    # ------------------------------------------------------------------
    op.create_table(
        "webhook_endpoint_events",
        sa.Column("endpoint_id", UUIDType, nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.PrimaryKeyConstraint("endpoint_id", "event_type"),
        sa.ForeignKeyConstraint(["endpoint_id"], ["webhook_endpoints.id"], ondelete="CASCADE"),
    )

    # ------------------------------------------------------------------
    # webhook_deliveries
    # ------------------------------------------------------------------
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", UUIDType, nullable=False),
        sa.Column("endpoint_id", UUIDType, nullable=True),
        sa.Column("job_id", UUIDType, nullable=True),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("url_override", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_attempt_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_status_code", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_retry_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["endpoint_id"], ["webhook_endpoints.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("job_id", "event_type", "endpoint_id", name="uq_webhook_deliveries_job_event_endpoint"),
        sa.UniqueConstraint("job_id", "event_type", "url_override", name="uq_webhook_deliveries_job_event_url"),
    )
    op.create_index("ix_webhook_deliveries_endpoint_id", "webhook_deliveries", ["endpoint_id"])
    op.create_index("ix_webhook_deliveries_job_id", "webhook_deliveries", ["job_id"])
    op.create_index("ix_webhook_deliveries_next_retry_at", "webhook_deliveries", ["next_retry_at"])

    # ------------------------------------------------------------------
    # pii_entity_types
    # ------------------------------------------------------------------
    op.create_table(
        "pii_entity_types",
        sa.Column("id", sa.String(50), nullable=False),
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("detection_method", sa.String(50), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pii_entity_types_category", "pii_entity_types", ["category"])

    # ------------------------------------------------------------------
    # artifact_objects
    # ------------------------------------------------------------------
    op.create_table(
        "artifact_objects",
        sa.Column("id", UUIDType, nullable=False),
        sa.Column("tenant_id", UUIDType, nullable=False),
        sa.Column("owner_type", sa.String(20), nullable=False),
        sa.Column("owner_id", UUIDType, nullable=False),
        sa.Column("artifact_type", sa.String(50), nullable=False),
        sa.Column("uri", sa.Text(), nullable=False),
        sa.Column("sensitivity", sa.String(20), nullable=False),
        sa.Column("store", sa.Boolean(), nullable=False),
        sa.Column("ttl_seconds", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("available_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("purge_after", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("purged_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
    )
    op.create_index("ix_artifact_objects_tenant_id", "artifact_objects", ["tenant_id"])

    # ------------------------------------------------------------------
    # artifact_compliance_tags (junction)
    # ------------------------------------------------------------------
    op.create_table(
        "artifact_compliance_tags",
        sa.Column("artifact_id", UUIDType, nullable=False),
        sa.Column("tag", sa.String(50), nullable=False),
        sa.PrimaryKeyConstraint("artifact_id", "tag"),
        sa.ForeignKeyConstraint(["artifact_id"], ["artifact_objects.id"], ondelete="CASCADE"),
    )

    # ------------------------------------------------------------------
    # settings
    # ------------------------------------------------------------------
    op.create_table(
        "settings",
        sa.Column("id", UUIDType, nullable=False),
        sa.Column("tenant_id", UUIDType, nullable=True),
        sa.Column("namespace", sa.String(50), nullable=False),
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_by", UUIDType, nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.UniqueConstraint("tenant_id", "namespace", "key", name="uq_settings_tenant_ns_key"),
    )
    op.create_index("ix_settings_tenant_id", "settings", ["tenant_id"])
    op.create_index("ix_settings_namespace", "settings", ["namespace"])

    # ------------------------------------------------------------------
    # realtime_sessions
    # ------------------------------------------------------------------
    op.create_table(
        "realtime_sessions",
        sa.Column("id", UUIDType, nullable=False),
        sa.Column("tenant_id", UUIDType, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("language", sa.String(10), nullable=True),
        sa.Column("model", sa.String(50), nullable=True),
        sa.Column("runtime", sa.String(50), nullable=True),
        sa.Column("encoding", sa.String(20), nullable=True),
        sa.Column("sample_rate", sa.Integer(), nullable=True),
        sa.Column("enhance_on_end", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("retention", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("audio_uri", sa.Text(), nullable=True),
        sa.Column("transcript_uri", sa.Text(), nullable=True),
        sa.Column("audio_duration_seconds", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("segment_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("word_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("instance", sa.String(100), nullable=True),
        sa.Column("client_ip", sa.String(45), nullable=True),
        sa.Column("previous_session_id", UUIDType, nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("ended_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("purge_after", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("purged_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_by_key_id", UUIDType, nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["previous_session_id"], ["realtime_sessions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_key_id"], ["api_keys.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_realtime_sessions_tenant_id", "realtime_sessions", ["tenant_id"])
    op.create_index("ix_realtime_sessions_status", "realtime_sessions", ["status"])
    op.create_index("ix_realtime_sessions_started_at", "realtime_sessions", ["started_at"])
    op.create_index("ix_realtime_sessions_created_by_key_id", "realtime_sessions", ["created_by_key_id"])

    # ------------------------------------------------------------------
    # models (model registry)
    # ------------------------------------------------------------------
    op.create_table(
        "models",
        sa.Column("id", sa.String(200), nullable=False),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("runtime", sa.String(50), nullable=False),
        sa.Column("runtime_model_id", sa.String(200), nullable=False),
        sa.Column("stage", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="not_downloaded"),
        sa.Column("download_path", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("downloaded_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("source", sa.String(200), nullable=True),
        sa.Column("library_name", sa.String(50), nullable=True),
        sa.Column("languages", sa.JSON(), nullable=True),
        sa.Column("word_timestamps", sa.Boolean(), server_default="false"),
        sa.Column("punctuation", sa.Boolean(), server_default="false"),
        sa.Column("capitalization", sa.Boolean(), server_default="false"),
        sa.Column("streaming", sa.Boolean(), server_default="false"),
        sa.Column("min_vram_gb", sa.Float(), nullable=True),
        sa.Column("min_ram_gb", sa.Float(), nullable=True),
        sa.Column("supports_cpu", sa.Boolean(), server_default="true"),
        sa.Column("model_metadata", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("metadata_source", sa.String(20), nullable=False, server_default="yaml"),
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_models_runtime", "models", ["runtime"])
    op.create_index("ix_models_stage", "models", ["stage"])
    op.create_index("ix_models_status", "models", ["status"])

    # ------------------------------------------------------------------
    # model_languages (junction)
    # ------------------------------------------------------------------
    op.create_table(
        "model_languages",
        sa.Column("model_id", sa.String(200), nullable=False),
        sa.Column("language_code", sa.String(10), nullable=False),
        sa.PrimaryKeyConstraint("model_id", "language_code"),
        sa.ForeignKeyConstraint(["model_id"], ["models.id"], ondelete="CASCADE"),
    )

    # ------------------------------------------------------------------
    # audit_log
    # ------------------------------------------------------------------
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("timestamp", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("correlation_id", sa.String(36), nullable=True),
        sa.Column("tenant_id", sa.Text(), nullable=True),
        sa.Column("actor_type", sa.String(20), nullable=False),
        sa.Column("actor_id", sa.Text(), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("resource_type", sa.String(30), nullable=False),
        sa.Column("resource_id", sa.Text(), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_log_timestamp", "audit_log", ["timestamp"])
    op.create_index("ix_audit_log_tenant_id", "audit_log", ["tenant_id"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("model_languages")
    op.drop_table("models")
    op.drop_table("realtime_sessions")
    op.drop_table("settings")
    op.drop_table("artifact_compliance_tags")
    op.drop_table("artifact_objects")
    op.drop_table("pii_entity_types")
    op.drop_table("webhook_deliveries")
    op.drop_table("webhook_endpoint_events")
    op.drop_table("webhook_endpoints")
    op.drop_table("task_dependencies")
    op.drop_table("tasks")
    op.drop_table("job_pii_entity_types")
    op.drop_table("jobs")
    op.drop_table("api_keys")
    op.drop_table("tenants")
