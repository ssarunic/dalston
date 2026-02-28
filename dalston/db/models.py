"""SQLAlchemy ORM models matching DATA_MODEL.md specification."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    ARRAY,
    TIMESTAMP,
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    pass


class RetentionPolicyModel(Base):
    """Retention policy for automated data lifecycle management."""

    __tablename__ = "retention_policies"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    tenant_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=True,  # NULL for system policies
        index=True,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    mode: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # auto_delete, keep, none
    hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scope: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="all"
    )  # all, audio_only
    realtime_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="inherit"
    )
    realtime_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delete_realtime_on_enhancement: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    is_system: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    tenant: Mapped["TenantModel | None"] = relationship(
        back_populates="retention_policies"
    )


class AuditLogModel(Base):
    """Immutable audit log entry for compliance and security tracking."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    timestamp: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )
    correlation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    tenant_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
        index=True,
    )
    actor_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # api_key, system, user
    actor_id: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True
    )  # job.created, etc.
    resource_type: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # job, session, api_key
    resource_id: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(INET, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)


class TenantModel(Base):
    """Tenant for multi-tenancy isolation."""

    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    jobs: Mapped[list["JobModel"]] = relationship(back_populates="tenant")
    api_keys: Mapped[list["APIKeyModel"]] = relationship(back_populates="tenant")
    webhook_endpoints: Mapped[list["WebhookEndpointModel"]] = relationship(
        back_populates="tenant"
    )
    realtime_sessions: Mapped[list["RealtimeSessionModel"]] = relationship(
        back_populates="tenant"
    )
    retention_policies: Mapped[list["RetentionPolicyModel"]] = relationship(
        back_populates="tenant"
    )


class JobModel(Base):
    """Batch transcription job."""

    __tablename__ = "jobs"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        index=True,
    )
    audio_uri: Mapped[str] = mapped_column(Text, nullable=False)
    # Audio metadata (extracted at upload time)
    audio_format: Mapped[str | None] = mapped_column(String(20), nullable=True)
    audio_duration: Mapped[float | None] = mapped_column(nullable=True)
    audio_sample_rate: Mapped[int | None] = mapped_column(nullable=True)
    audio_channels: Mapped[int | None] = mapped_column(nullable=True)
    audio_bit_depth: Mapped[int | None] = mapped_column(nullable=True)
    parameters: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )

    # Retention: 0=transient, -1=permanent, N=days
    retention: Mapped[int] = mapped_column(Integer, nullable=False, server_default="30")
    purge_after: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    purged_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # Legacy retention fields (M25) - kept for backwards compatibility
    retention_policy_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("retention_policies.id"),
        nullable=True,
    )
    retention_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="auto_delete"
    )
    retention_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retention_scope: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="all"
    )

    # Result summary stats (populated on successful completion)
    result_language_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    result_word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_segment_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_speaker_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_character_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # PII detection fields (M26)
    pii_detection_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    pii_entity_types: Mapped[list[str] | None] = mapped_column(
        ARRAY(String), nullable=True
    )
    pii_redact_audio: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    pii_redaction_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    pii_entities_detected: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pii_redacted_audio_uri: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Retry tracking
    retry_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    retried_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # Relationships
    tenant: Mapped["TenantModel"] = relationship(back_populates="jobs")
    tasks: Mapped[list["TaskModel"]] = relationship(back_populates="job")
    retention_policy: Mapped["RetentionPolicyModel | None"] = relationship()


class TaskModel(Base):
    """Atomic processing unit within a job's DAG."""

    __tablename__ = "tasks"
    __table_args__ = (
        # Prevent duplicate tasks for the same job+stage (multi-orchestrator safety)
        UniqueConstraint("job_id", "stage", name="uq_tasks_job_id_stage"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    job_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    stage: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    engine_id: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        index=True,
    )
    dependencies: Mapped[list[UUID]] = mapped_column(
        ARRAY(PG_UUID(as_uuid=True)),
        nullable=False,
        server_default="{}",
    )
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    input_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    retries: Mapped[int] = mapped_column(nullable=False, default=0)
    max_retries: Mapped[int] = mapped_column(nullable=False, default=2)
    required: Mapped[bool] = mapped_column(nullable=False, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )

    # Relationships
    job: Mapped["JobModel"] = relationship(back_populates="tasks")


class APIKeyModel(Base):
    """API key for authentication."""

    __tablename__ = "api_keys"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    key_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
    )
    prefix: Mapped[str] = mapped_column(String(10), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    scopes: Mapped[str] = mapped_column(String(255), nullable=False)
    rate_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )

    # Relationships
    tenant: Mapped["TenantModel"] = relationship(back_populates="api_keys")


class WebhookEndpointModel(Base):
    """Registered webhook endpoint for event delivery."""

    __tablename__ = "webhook_endpoints"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    events: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
    )
    signing_secret: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    disabled_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    last_success_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    tenant: Mapped["TenantModel"] = relationship(back_populates="webhook_endpoints")
    deliveries: Mapped[list["WebhookDeliveryModel"]] = relationship(
        back_populates="endpoint"
    )


class WebhookDeliveryModel(Base):
    """Webhook delivery attempt record."""

    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "event_type",
            "endpoint_id",
            name="uq_webhook_deliveries_job_event_endpoint",
        ),
        UniqueConstraint(
            "job_id",
            "event_type",
            "url_override",
            name="uq_webhook_deliveries_job_event_url",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    endpoint_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("webhook_endpoints.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    job_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    url_override: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_attempt_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )
    last_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Relationships
    endpoint: Mapped["WebhookEndpointModel | None"] = relationship(
        back_populates="deliveries"
    )
    job: Mapped["JobModel | None"] = relationship()


class PIIEntityTypeModel(Base):
    """PII entity type reference table for validation and UI display."""

    __tablename__ = "pii_entity_types"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    category: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    detection_method: Mapped[str] = mapped_column(String(50), nullable=False)
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )


# =============================================================================
# Artifact Model (Simplified Retention)
# =============================================================================


class ArtifactObjectModel(Base):
    """Persisted artifact with retention metadata."""

    __tablename__ = "artifact_objects"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    owner_type: Mapped[str] = mapped_column(String(20), nullable=False)  # job | session
    owner_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    artifact_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # audio.source, transcript.redacted, etc.
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    sensitivity: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # raw_pii | redacted | metadata
    compliance_tags: Mapped[list[str] | None] = mapped_column(
        ARRAY(String), nullable=True
    )  # gdpr, hipaa, pci, pii-processed
    store: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ttl_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    available_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    purge_after: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    purged_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # Relationships
    tenant: Mapped["TenantModel"] = relationship()


class SettingModel(Base):
    """Admin-configurable setting stored in the database.

    Settings are organized by namespace (e.g., rate_limits, engines).
    Database values override environment variable defaults at runtime.
    """

    __tablename__ = "settings"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "namespace", "key", name="uq_settings_tenant_ns_key"
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )
    tenant_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=True,
        index=True,
    )
    namespace: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[dict] = mapped_column(JSONB, nullable=False)
    updated_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    tenant: Mapped["TenantModel | None"] = relationship()


class RealtimeSessionModel(Base):
    """Real-time transcription session with optional persistence."""

    __tablename__ = "realtime_sessions"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
    )
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )

    # Status: active, completed, error, interrupted
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="active",
        index=True,
    )

    # Parameters (immutable after creation)
    language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    model: Mapped[str | None] = mapped_column(String(50), nullable=True)
    engine: Mapped[str | None] = mapped_column(String(50), nullable=True)
    encoding: Mapped[str | None] = mapped_column(String(20), nullable=True)
    sample_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Enhancement option (process through batch pipeline after session ends)
    enhance_on_end: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Retention: 0=transient, -1=permanent, N=days
    retention: Mapped[int] = mapped_column(Integer, nullable=False, server_default="30")

    # Results (populated during/after session)
    audio_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    transcript_uri: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Stats (updated periodically during session)
    audio_duration_seconds: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    segment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Tracking
    worker_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    client_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    previous_session_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("realtime_sessions.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Timestamps
    started_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=True,
    )

    # Error tracking
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Purge tracking
    purge_after: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    purged_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # Legacy retention fields (M25) - kept for backwards compatibility
    retention_policy_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("retention_policies.id"),
        nullable=True,
    )
    retention_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="auto_delete"
    )
    retention_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Relationships
    tenant: Mapped["TenantModel"] = relationship()
    previous_session: Mapped["RealtimeSessionModel | None"] = relationship(
        remote_side="RealtimeSessionModel.id"
    )
    retention_policy: Mapped["RetentionPolicyModel | None"] = relationship()
