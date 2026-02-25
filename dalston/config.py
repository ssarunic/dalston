from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Webhook constants
WEBHOOK_SECRET_DEFAULT = "dalston-webhook-secret-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # PostgreSQL
    database_url: str = Field(
        default="postgresql+asyncpg://dalston:password@localhost:5432/dalston",
        alias="DATABASE_URL",
    )

    # Redis
    redis_url: str = Field(
        default="redis://localhost:6379",
        alias="REDIS_URL",
    )

    # S3 Storage
    s3_bucket: str = Field(default="dalston-artifacts", alias="DALSTON_S3_BUCKET")
    s3_region: str = Field(default="eu-west-2", alias="DALSTON_S3_REGION")
    s3_endpoint_url: str | None = Field(default=None, alias="DALSTON_S3_ENDPOINT_URL")
    s3_public_endpoint_url: str | None = Field(
        default=None,
        alias="DALSTON_S3_PUBLIC_ENDPOINT_URL",
        description=(
            "Browser-reachable S3/MinIO endpoint used for presigned download URLs. "
            "If unset, the backend uses DALSTON_S3_ENDPOINT_URL and local MinIO fallback logic."
        ),
    )

    # AWS Credentials (optional, can use IAM roles)
    aws_access_key_id: str | None = Field(default=None, alias="AWS_ACCESS_KEY_ID")
    aws_secret_access_key: str | None = Field(
        default=None, alias="AWS_SECRET_ACCESS_KEY"
    )

    # Webhooks
    webhook_secret: str = Field(
        default=WEBHOOK_SECRET_DEFAULT,
        alias="DALSTON_WEBHOOK_SECRET",
        description="Default HMAC secret for signing webhook payloads (used for endpoints without custom secrets)",
    )

    # Rate Limiting
    rate_limit_requests_per_minute: int = Field(
        default=600,
        alias="DALSTON_RATE_LIMIT_REQUESTS_PER_MINUTE",
        description="Maximum API requests per minute per tenant",
    )
    rate_limit_concurrent_jobs: int = Field(
        default=10,
        alias="DALSTON_RATE_LIMIT_CONCURRENT_JOBS",
        description="Maximum concurrent batch transcription jobs per tenant",
    )
    rate_limit_concurrent_sessions: int = Field(
        default=5,
        alias="DALSTON_RATE_LIMIT_CONCURRENT_SESSIONS",
        description="Maximum concurrent realtime sessions per tenant",
    )

    # Data Retention (M25)
    retention_cleanup_interval_seconds: int = Field(
        default=300,  # 5 minutes
        alias="DALSTON_RETENTION_CLEANUP_INTERVAL_SECONDS",
        description="Interval between cleanup worker sweeps",
    )
    retention_cleanup_batch_size: int = Field(
        default=100,
        alias="DALSTON_RETENTION_CLEANUP_BATCH_SIZE",
        description="Maximum jobs to purge per cleanup sweep",
    )
    retention_min_hours: int = Field(
        default=1,
        alias="DALSTON_RETENTION_MIN_HOURS",
        description="Minimum retention hours allowed (1 = 1 hour minimum)",
    )

    # Engine Availability Behavior
    engine_unavailable_behavior: Literal["fail_fast", "wait"] = Field(
        default="fail_fast",
        alias="DALSTON_ENGINE_UNAVAILABLE_BEHAVIOR",
        description=(
            "Behavior when a required engine is not running. "
            "'fail_fast': fail immediately with error (default). "
            "'wait': queue task and wait for engine to start."
        ),
    )
    engine_wait_timeout_seconds: int = Field(
        default=300,
        alias="DALSTON_ENGINE_WAIT_TIMEOUT_SECONDS",
        description=(
            "Maximum time to wait for an engine to start (only used when "
            "engine_unavailable_behavior='wait'). Task fails if engine "
            "doesn't pick it up within this timeout."
        ),
    )

    # Audio URL Download
    audio_url_max_size_gb: float = Field(
        default=3.0,
        alias="DALSTON_AUDIO_URL_MAX_SIZE_GB",
        description="Maximum audio file size for URL downloads in GB",
    )
    audio_url_timeout_seconds: int = Field(
        default=300,
        alias="DALSTON_AUDIO_URL_TIMEOUT_SECONDS",
        description="Timeout for downloading audio from URLs in seconds",
    )


@lru_cache
def get_settings() -> Settings:
    """Get cached application settings.

    Settings are read from environment variables and .env file once,
    then cached for the lifetime of the process.
    """
    return Settings()


def warn_if_default_webhook_secret(settings: Settings) -> None:
    """Log a warning if the default webhook secret is being used.

    Should be called at application startup in production environments.
    """
    import logging

    if settings.webhook_secret == WEBHOOK_SECRET_DEFAULT:
        logging.warning(
            "Using default webhook secret. Set DALSTON_WEBHOOK_SECRET environment variable "
            "for production use."
        )
