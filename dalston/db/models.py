"""SQLAlchemy ORM models matching DATA_MODEL.md specification."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
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
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from dalston.db.types import InetType, JSONType, UUIDType


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    pass


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
        UUIDType,
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
    detail: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(InetType, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)


class TenantModel(Base):
    """Tenant for multi-tenancy isolation."""

    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(
        UUIDType,
        primary_key=True,
        default=uuid4,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    settings: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
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


class JobModel(Base):
    """Batch transcription job."""

    __tablename__ = "jobs"

    id: Mapped[UUID] = mapped_column(
        UUIDType,
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[UUID] = mapped_column(
        UUIDType,
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
    display_name: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default=""
    )
    audio_uri: Mapped[str] = mapped_column(Text, nullable=False)
    # Audio metadata (extracted at upload time)
    audio_format: Mapped[str | None] = mapped_column(String(20), nullable=True)
    audio_duration: Mapped[float | None] = mapped_column(nullable=True)
    audio_sample_rate: Mapped[int | None] = mapped_column(nullable=True)
    audio_channels: Mapped[int | None] = mapped_column(nullable=True)
    audio_bit_depth: Mapped[int | None] = mapped_column(nullable=True)
    parameters: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
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
    pii_redact_audio: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    pii_redaction_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    pii_entities_detected: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pii_redacted_audio_uri: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Typed job parameters (M57.0 Phase 3 — parallel to parameters JSON blob)
    param_language: Mapped[str | None] = mapped_column(String(10), nullable=True)
    param_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    param_word_timestamps: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    param_timestamps_granularity: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )
    param_speaker_detection: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )
    param_num_speakers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    param_min_speakers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    param_max_speakers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    param_beam_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    param_vad_filter: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    param_exclusive: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    param_num_channels: Mapped[int | None] = mapped_column(Integer, nullable=True)
    param_pii_confidence_threshold: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    param_pii_buffer_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    param_transcribe_config: Mapped[dict | None] = mapped_column(
        JSONType, nullable=True
    )

    # Ownership tracking (M45)
    created_by_key_id: Mapped[UUID | None] = mapped_column(
        UUIDType,
        ForeignKey("api_keys.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Relationships
    tenant: Mapped["TenantModel"] = relationship(back_populates="jobs")
    tasks: Mapped[list["TaskModel"]] = relationship(back_populates="job")
    pii_entity_type_links: Mapped[list["JobPIIEntityType"]] = relationship(
        "JobPIIEntityType",
        foreign_keys="[JobPIIEntityType.job_id]",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    @property
    def pii_entity_types(self) -> list[str] | None:
        """Backward-compatible list of PII entity type strings."""
        result = [link.entity_type_id for link in self.pii_entity_type_links]
        return result if result else None


class JobPIIEntityType(Base):
    """Junction table: PII entity types requested for a job."""

    __tablename__ = "job_pii_entity_types"

    job_id: Mapped[UUID] = mapped_column(
        UUIDType,
        ForeignKey("jobs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    entity_type_id: Mapped[str] = mapped_column(String(50), primary_key=True)


class TaskModel(Base):
    """Atomic processing unit within a job's DAG."""

    __tablename__ = "tasks"
    __table_args__ = (
        # Prevent duplicate tasks for the same job+stage (multi-orchestrator safety)
        UniqueConstraint("job_id", "stage", name="uq_tasks_job_id_stage"),
    )

    id: Mapped[UUID] = mapped_column(
        UUIDType,
        primary_key=True,
        default=uuid4,
    )
    job_id: Mapped[UUID] = mapped_column(
        UUIDType,
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
    config: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
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
    dependency_links: Mapped[list["TaskDependency"]] = relationship(
        "TaskDependency",
        foreign_keys="[TaskDependency.task_id]",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    @property
    def dependencies(self) -> list[UUID]:
        """Backward-compatible list of dependency task UUIDs."""
        return [d.depends_on_id for d in self.dependency_links]


class TaskDependency(Base):
    """Junction table: DAG dependency edges between tasks."""

    __tablename__ = "task_dependencies"

    task_id: Mapped[UUID] = mapped_column(
        UUIDType,
        ForeignKey("tasks.id", ondelete="CASCADE"),
        primary_key=True,
    )
    depends_on_id: Mapped[UUID] = mapped_column(
        UUIDType,
        ForeignKey("tasks.id", ondelete="CASCADE"),
        primary_key=True,
    )


class APIKeyModel(Base):
    """API key for authentication."""

    __tablename__ = "api_keys"

    id: Mapped[UUID] = mapped_column(
        UUIDType,
        primary_key=True,
        default=uuid4,
    )
    key_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        unique=True,
    )
    prefix: Mapped[str] = mapped_column(String(10), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(
        UUIDType,
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

    # Ownership tracking (M45) - which API key created this key
    created_by_key_id: Mapped[UUID | None] = mapped_column(
        UUIDType,
        ForeignKey("api_keys.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Relationships
    tenant: Mapped["TenantModel"] = relationship(back_populates="api_keys")


class WebhookEndpointModel(Base):
    """Registered webhook endpoint for event delivery."""

    __tablename__ = "webhook_endpoints"

    id: Mapped[UUID] = mapped_column(
        UUIDType,
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[UUID] = mapped_column(
        UUIDType,
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
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

    # Ownership tracking (M45)
    created_by_key_id: Mapped[UUID | None] = mapped_column(
        UUIDType,
        ForeignKey("api_keys.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Relationships
    tenant: Mapped["TenantModel"] = relationship(back_populates="webhook_endpoints")
    deliveries: Mapped[list["WebhookDeliveryModel"]] = relationship(
        back_populates="endpoint"
    )
    endpoint_events: Mapped[list["WebhookEndpointEvent"]] = relationship(
        "WebhookEndpointEvent",
        foreign_keys="[WebhookEndpointEvent.endpoint_id]",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    @property
    def events(self) -> list[str]:
        """Backward-compatible list of subscribed event type strings."""
        return [e.event_type for e in self.endpoint_events]

    @events.setter
    def events(self, event_types: list[str]) -> None:
        """Populate endpoint_events from a list of event type strings."""
        self.endpoint_events = [
            WebhookEndpointEvent(event_type=et) for et in event_types
        ]


class WebhookEndpointEvent(Base):
    """Junction table: event types subscribed by a webhook endpoint."""

    __tablename__ = "webhook_endpoint_events"

    endpoint_id: Mapped[UUID] = mapped_column(
        UUIDType,
        ForeignKey("webhook_endpoints.id", ondelete="CASCADE"),
        primary_key=True,
    )
    event_type: Mapped[str] = mapped_column(String(50), primary_key=True)


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
        UUIDType,
        primary_key=True,
        default=uuid4,
    )
    endpoint_id: Mapped[UUID | None] = mapped_column(
        UUIDType,
        ForeignKey("webhook_endpoints.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    job_id: Mapped[UUID | None] = mapped_column(
        UUIDType,
        ForeignKey("jobs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONType, nullable=False)
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
        UUIDType,
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[UUID] = mapped_column(
        UUIDType,
        ForeignKey("tenants.id"),
        nullable=False,
        index=True,
    )
    owner_type: Mapped[str] = mapped_column(String(20), nullable=False)  # job | session
    owner_id: Mapped[UUID] = mapped_column(UUIDType, nullable=False)
    artifact_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # audio.source, transcript.redacted, etc.
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    sensitivity: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # raw_pii | redacted | metadata
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
    compliance_tag_links: Mapped[list["ArtifactComplianceTag"]] = relationship(
        "ArtifactComplianceTag",
        foreign_keys="[ArtifactComplianceTag.artifact_id]",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    @property
    def compliance_tags(self) -> list[str] | None:
        """Backward-compatible list of compliance tag strings."""
        result = [t.tag for t in self.compliance_tag_links]
        return result if result else None


class ArtifactComplianceTag(Base):
    """Junction table: compliance tags applied to an artifact."""

    __tablename__ = "artifact_compliance_tags"

    artifact_id: Mapped[UUID] = mapped_column(
        UUIDType,
        ForeignKey("artifact_objects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tag: Mapped[str] = mapped_column(String(50), primary_key=True)


class SettingModel(Base):
    """Admin-configurable setting stored in the database.

    Settings are organized by namespace (e.g., rate_limits, engines).
    Database values override environment variable defaults at engine_id.
    """

    __tablename__ = "settings"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "namespace", "key", name="uq_settings_tenant_ns_key"
        ),
    )

    id: Mapped[UUID] = mapped_column(
        UUIDType,
        primary_key=True,
        default=uuid4,
    )
    tenant_id: Mapped[UUID | None] = mapped_column(
        UUIDType,
        ForeignKey("tenants.id"),
        nullable=True,
        index=True,
    )
    namespace: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[dict] = mapped_column(JSONType, nullable=False)
    updated_by: Mapped[UUID | None] = mapped_column(
        UUIDType,
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
        UUIDType,
        primary_key=True,
    )
    tenant_id: Mapped[UUID] = mapped_column(
        UUIDType,
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
    engine_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    encoding: Mapped[str | None] = mapped_column(String(20), nullable=True)
    sample_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)

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
    instance: Mapped[str | None] = mapped_column(String(100), nullable=True)
    client_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    previous_session_id: Mapped[UUID | None] = mapped_column(
        UUIDType,
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

    # Ownership tracking (M45)
    created_by_key_id: Mapped[UUID | None] = mapped_column(
        UUIDType,
        ForeignKey("api_keys.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Relationships
    tenant: Mapped["TenantModel"] = relationship()
    previous_session: Mapped["RealtimeSessionModel | None"] = relationship(
        remote_side="RealtimeSessionModel.id"
    )


# =============================================================================
# Model Registry (M40)
# =============================================================================


class ModelRegistryModel(Base):
    """Model registry entry tracking available models and their status.

    This table tracks ML models available to Dalston engines:
    - Download status (not_downloaded, downloading, ready, failed)
    - Runtime mapping (which engine can load this model)
    - Capabilities (word timestamps, punctuation, streaming)
    - Hardware requirements (VRAM, RAM, CPU support)
    - HuggingFace metadata cache

    Models are identified by a namespaced model ID (e.g., "nvidia/parakeet-tdt-1.1b")
    matching the HuggingFace model ID.
    """

    __tablename__ = "models"

    # Identity - namespaced model ID (e.g., "nvidia/parakeet-tdt-1.1b")
    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Runtime mapping - which engine engine_id loads this model
    engine_id: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    loaded_model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    stage: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # Download status
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="not_downloaded", index=True
    )
    download_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    expected_total_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    downloaded_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    progress_updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )
    downloaded_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # Source and library info (for HuggingFace card routing)
    source: Mapped[str | None] = mapped_column(String(200), nullable=True)
    library_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Kept as nullable JSON for backward compat; canonical data in model_languages
    languages: Mapped[list | None] = mapped_column(JSONType, nullable=True)

    # Capabilities
    word_timestamps: Mapped[bool] = mapped_column(Boolean, server_default="false")
    punctuation: Mapped[bool] = mapped_column(Boolean, server_default="false")
    capitalization: Mapped[bool] = mapped_column(Boolean, server_default="false")
    streaming: Mapped[bool] = mapped_column(Boolean, server_default="false")

    # Hardware requirements
    min_vram_gb: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_ram_gb: Mapped[float | None] = mapped_column(Float, nullable=True)
    supports_cpu: Mapped[bool] = mapped_column(Boolean, server_default="true")

    # Metadata cache (HuggingFace card data, download stats, etc.)
    model_metadata: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)

    # Provenance tracking - where did this model's metadata come from?
    # Values: "yaml" (from YAML files), "user" (manually enriched), "hf" (HuggingFace)
    metadata_source: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="yaml"
    )

    # Usage tracking
    last_used_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    language_links: Mapped[list["ModelLanguage"]] = relationship(
        "ModelLanguage",
        foreign_keys="[ModelLanguage.model_id]",
        lazy="selectin",
        cascade="all, delete-orphan",
    )


class ModelLanguage(Base):
    """Junction table: languages supported by a model registry entry."""

    __tablename__ = "model_languages"

    model_id: Mapped[str] = mapped_column(
        String(200),
        ForeignKey("models.id", ondelete="CASCADE"),
        primary_key=True,
    )
    language_code: Mapped[str] = mapped_column(String(10), primary_key=True)
