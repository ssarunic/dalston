"""FastAPI dependency injection functions."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

import structlog
from fastapi import Depends, HTTPException, Request
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.common.redis import get_redis as _get_redis_client
from dalston.config import Settings
from dalston.config import get_settings as _get_settings
from dalston.db.models import APIKeyModel
from dalston.db.session import async_session
from dalston.gateway.error_codes import Err
from dalston.gateway.middleware.auth import authenticate_request
from dalston.gateway.security.manager import SecurityManager
from dalston.gateway.security.manager import (
    get_security_manager as _get_security_manager,
)
from dalston.gateway.security.principal import Principal
from dalston.gateway.services.audit_query import AuditQueryService
from dalston.gateway.services.auth import APIKey, AuthService, Scope
from dalston.gateway.services.console import ConsoleService
from dalston.gateway.services.export import ExportService
from dalston.gateway.services.ingestion import AudioIngestionService
from dalston.gateway.services.jobs import JobsService
from dalston.gateway.services.pii_entity_types import PIIEntityTypeService
from dalston.gateway.services.rate_limiter import RateLimitResult, RedisRateLimiter
from dalston.gateway.services.storage import StorageService

if TYPE_CHECKING:
    from dalston.common.audit import AuditService
    from dalston.orchestrator.session_coordinator import SessionCoordinator

logger = structlog.get_logger()


def _build_rate_limit_headers(
    limit: int,
    remaining: int,
    reset_seconds: int | None = None,
) -> dict[str, str]:
    """Build OpenAI+legacy rate-limit headers."""
    headers = {
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Limit-Requests": str(limit),
        "X-RateLimit-Remaining-Requests": str(remaining),
    }
    if reset_seconds is not None:
        headers["X-RateLimit-Reset-Requests"] = str(reset_seconds)
    return headers


class _NoopRedis:
    """Minimal async no-op Redis client for lite mode endpoints."""

    async def publish(self, *_args, **_kwargs) -> int:
        return 0

    async def close(self) -> None:
        return None

    def __getattr__(self, method_name: str):
        async def _noop(*_args, **_kwargs):
            logger.debug("noop_redis_method_called", method=method_name)
            return None

        return _noop


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async database session."""
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()


async def get_redis() -> Redis:
    """Get async Redis client."""
    if _get_settings().runtime_mode == "lite":
        return _NoopRedis()  # type: ignore[return-value]
    return await _get_redis_client()


def get_settings() -> Settings:
    """Get application settings."""
    return _get_settings()


# Service singletons for dependency injection
_jobs_service: JobsService | None = None
_export_service: ExportService | None = None
_audit_service: AuditService | None = None
_console_service: ConsoleService | None = None
_audit_query_service: AuditQueryService | None = None
_pii_entity_type_service: PIIEntityTypeService | None = None


def get_jobs_service() -> JobsService:
    """Get JobsService instance (singleton)."""
    global _jobs_service
    if _jobs_service is None:
        _jobs_service = JobsService()
    return _jobs_service


def get_export_service() -> ExportService:
    """Get ExportService instance (singleton)."""
    global _export_service
    if _export_service is None:
        _export_service = ExportService()
    return _export_service


def get_console_service() -> ConsoleService:
    """Get ConsoleService instance (singleton)."""
    global _console_service
    if _console_service is None:
        _console_service = ConsoleService()
    return _console_service


def get_audit_query_service() -> AuditQueryService:
    """Get AuditQueryService instance (singleton)."""
    global _audit_query_service
    if _audit_query_service is None:
        _audit_query_service = AuditQueryService()
    return _audit_query_service


def get_pii_entity_type_service() -> PIIEntityTypeService:
    """Get PIIEntityTypeService instance (singleton)."""
    global _pii_entity_type_service
    if _pii_entity_type_service is None:
        _pii_entity_type_service = PIIEntityTypeService()
    return _pii_entity_type_service


def get_storage_service(
    settings: Settings = Depends(get_settings),
) -> StorageService:
    """Get StorageService instance."""
    return StorageService(settings)


def get_ingestion_service(
    settings: Settings = Depends(get_settings),
) -> AudioIngestionService:
    """Get AudioIngestionService instance.

    Not a singleton since it depends on Settings which may vary in tests.
    """
    return AudioIngestionService(settings)


def get_audit_service() -> AuditService:
    """Get AuditService instance (singleton).

    The audit service uses its own database session factory to ensure
    audit writes are independent of the request's transaction.
    """
    global _audit_service
    if _audit_service is None:
        from contextlib import asynccontextmanager

        from dalston.common.audit import AuditService

        @asynccontextmanager
        async def db_session_factory():
            async with async_session() as session:
                yield session

        _audit_service = AuditService(db_session_factory)
    return _audit_service


def get_session_router() -> SessionCoordinator:
    """Get session coordinator instance.

    The coordinator is initialised in main.py lifespan and stored globally.
    """
    from dalston.gateway.main import session_router

    if session_router is None:
        raise HTTPException(
            status_code=503,
            detail=Err.SESSION_ROUTER_NOT_INITIALIZED,
        )
    return session_router


async def get_auth_service(
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> AuthService:
    """Get AuthService instance.

    Creates a new AuthService per request with database and Redis connections.
    API keys are stored in PostgreSQL, session tokens and rate limits in Redis.
    """
    return AuthService(db, redis)


def _get_dev_api_key() -> APIKey:
    """Create a development API key for security_mode=none.

    This is only used in development and returns an API key
    with full admin permissions.
    """
    from datetime import UTC, datetime
    from uuid import UUID

    from dalston.db.session import DEFAULT_TENANT_ID

    # Well-known dev key ID (deterministic for testing)
    DEV_KEY_ID = UUID("00000000-0000-0000-0000-000000000002")

    return APIKey(
        id=DEV_KEY_ID,
        key_hash="dev_key_hash",
        prefix="dk_dev0000",
        name="Development Key",
        tenant_id=DEFAULT_TENANT_ID,
        scopes=[Scope.ADMIN],  # Full access in dev mode
        rate_limit=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        last_used_at=None,
        expires_at=datetime(2099, 12, 31, 23, 59, 59, tzinfo=UTC),
        revoked_at=None,
    )


async def _ensure_dev_api_key_record(db: AsyncSession, api_key: APIKey) -> None:
    """Ensure the development key exists in DB for FK-backed job writes."""
    stmt = select(APIKeyModel.id).where(APIKeyModel.id == api_key.id)
    existing = await db.execute(stmt)
    if existing.scalar_one_or_none() is not None:
        return

    db.add(
        APIKeyModel(
            id=api_key.id,
            key_hash=api_key.key_hash,
            prefix=api_key.prefix,
            name=api_key.name,
            tenant_id=api_key.tenant_id,
            scopes=",".join(scope.value for scope in api_key.scopes),
            rate_limit=api_key.rate_limit,
            created_at=api_key.created_at,
            last_used_at=api_key.last_used_at,
            expires_at=api_key.expires_at,
            revoked_at=api_key.revoked_at,
        )
    )
    try:
        await db.commit()
    except IntegrityError:
        # Another concurrent request inserted the dev key first.
        await db.rollback()


async def require_auth(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> APIKey:
    """Dependency that requires a valid API key.

    Extracts and validates the API key from the request.
    Attaches api_key and tenant_id to request.state.

    In security_mode=none (development only), returns a dev API key
    with admin permissions without requiring authentication.

    Returns:
        Validated APIKey object

    Raises:
        HTTPException 401: If key is missing or invalid
        HTTPException 429: If rate limit exceeded
    """
    security_manager = _get_security_manager()
    if security_manager.mode == "none":
        # Development mode: return dev API key with admin access
        dev_key = _get_dev_api_key()
        if _get_settings().runtime_mode != "lite":
            await _ensure_dev_api_key_record(db, dev_key)
        request.state.api_key = dev_key
        request.state.tenant_id = dev_key.tenant_id
        return dev_key

    redis = await _get_redis_client()
    auth_service = AuthService(db, redis)
    return await authenticate_request(request, auth_service)


# =============================================================================
# Security Manager Dependencies (M45)
# =============================================================================


def get_security_manager() -> SecurityManager:
    """Get SecurityManager instance for authorization checks."""
    return _get_security_manager()


async def get_principal(
    api_key: APIKey = Depends(require_auth),
) -> Principal:
    """Get authenticated Principal from request.

    Converts API key or session token into a Principal for use
    in authorization checks.
    """
    from dalston.gateway.services.auth import SessionToken

    if isinstance(api_key, SessionToken):
        return Principal.from_session_token(api_key)
    return Principal.from_api_key(api_key)


# =============================================================================
# Rate limiter singleton
_rate_limiter: RedisRateLimiter | None = None
_lite_rate_limiter: _NoopRateLimiter | None = None


class RateLimiter(Protocol):
    """Shared rate limiter interface used by both Redis and lite no-op impls."""

    async def check_request_rate(self, tenant_id: UUID) -> RateLimitResult: ...

    async def check_concurrent_jobs(self, tenant_id: UUID) -> RateLimitResult: ...

    async def check_concurrent_sessions(self, tenant_id: UUID) -> RateLimitResult: ...

    async def increment_concurrent_jobs(self, tenant_id: UUID) -> None: ...

    async def decrement_concurrent_jobs(self, tenant_id: UUID) -> None: ...

    async def decrement_concurrent_jobs_once(
        self, job_id: UUID, tenant_id: UUID
    ) -> bool: ...

    async def increment_concurrent_sessions(self, tenant_id: UUID) -> None: ...

    async def decrement_concurrent_sessions(self, tenant_id: UUID) -> None: ...


class _NoopRateLimiter:
    """Rate limiter that always allows requests (lite mode)."""

    def __init__(
        self,
        requests_per_minute: int,
        max_concurrent_jobs: int,
        max_concurrent_sessions: int,
    ) -> None:
        self._requests_per_minute = requests_per_minute
        self._max_concurrent_jobs = max_concurrent_jobs
        self._max_concurrent_sessions = max_concurrent_sessions

    async def check_request_rate(self, _tenant_id: UUID) -> RateLimitResult:
        return RateLimitResult(
            allowed=True,
            limit=self._requests_per_minute,
            remaining=self._requests_per_minute,
            reset_seconds=60,
        )

    async def check_concurrent_jobs(self, _tenant_id: UUID) -> RateLimitResult:
        return RateLimitResult(
            allowed=True,
            limit=self._max_concurrent_jobs,
            remaining=self._max_concurrent_jobs,
        )

    async def check_concurrent_sessions(self, _tenant_id: UUID) -> RateLimitResult:
        return RateLimitResult(
            allowed=True,
            limit=self._max_concurrent_sessions,
            remaining=self._max_concurrent_sessions,
        )

    async def increment_concurrent_jobs(self, _tenant_id: UUID) -> None:
        return None

    async def decrement_concurrent_jobs(self, _tenant_id: UUID) -> None:
        return None

    async def decrement_concurrent_jobs_once(
        self, _job_id: UUID, _tenant_id: UUID
    ) -> bool:
        return True

    async def increment_concurrent_sessions(self, _tenant_id: UUID) -> None:
        return None

    async def decrement_concurrent_sessions(self, _tenant_id: UUID) -> None:
        return None


def set_rate_limiter(limiter: RedisRateLimiter | None) -> None:
    """Set the rate limiter instance (for testing)."""
    global _rate_limiter
    _rate_limiter = limiter


def reset_rate_limiter() -> None:
    """Reset the rate limiter singleton (for testing)."""
    global _rate_limiter, _lite_rate_limiter
    _rate_limiter = None
    _lite_rate_limiter = None


async def get_rate_limiter(
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
) -> RateLimiter:
    """Get RateLimiter instance.

    Creates a singleton rate limiter with Redis backend.
    On each request, limits are refreshed from DB overrides (5s TTL cache).
    """
    global _rate_limiter, _lite_rate_limiter

    if settings.runtime_mode == "lite":
        if _lite_rate_limiter is None:
            _lite_rate_limiter = _NoopRateLimiter(
                requests_per_minute=settings.rate_limit_requests_per_minute,
                max_concurrent_jobs=settings.rate_limit_concurrent_jobs,
                max_concurrent_sessions=settings.rate_limit_concurrent_sessions,
            )
        return _lite_rate_limiter

    redis = await _get_redis_client()
    if _rate_limiter is None:
        _rate_limiter = RedisRateLimiter(
            redis=redis,
            requests_per_minute=settings.rate_limit_requests_per_minute,
            max_concurrent_jobs=settings.rate_limit_concurrent_jobs,
            max_concurrent_sessions=settings.rate_limit_concurrent_sessions,
        )

    # Refresh limits from DB settings (uses 5s TTL cache — essentially free)
    try:
        from dalston.gateway.services.settings import SettingsService

        svc = SettingsService()
        _rate_limiter._requests_per_minute = await svc.get_effective_value(
            db, "rate_limits", "requests_per_minute"
        )
        _rate_limiter._max_concurrent_jobs = await svc.get_effective_value(
            db, "rate_limits", "concurrent_jobs"
        )
        _rate_limiter._max_concurrent_sessions = await svc.get_effective_value(
            db, "rate_limits", "concurrent_sessions"
        )
    except (OSError, TimeoutError) as e:
        # Transient errors (network, timeout) - keep existing limits
        logger.warning("failed_to_refresh_rate_limits", error=str(e))
    except Exception:
        # Unexpected error (likely a bug) - re-raise to fail the request
        logger.error("failed_to_refresh_rate_limits", exc_info=True)
        raise

    return _rate_limiter


async def check_request_rate_limit(
    api_key: APIKey = Depends(require_auth),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
) -> APIKey:
    """Dependency that checks request rate limit.

    Raises HTTPException 429 if rate limit exceeded.
    """
    result = await rate_limiter.check_request_rate(api_key.tenant_id)
    if not result.allowed:
        headers = _build_rate_limit_headers(
            limit=result.limit,
            remaining=result.remaining,
            reset_seconds=result.reset_seconds,
        )
        headers["Retry-After"] = str(result.reset_seconds)
        raise HTTPException(
            status_code=429,
            detail=Err.RATE_LIMIT_EXCEEDED,
            headers=headers,
        )
    return api_key


async def check_concurrent_jobs_limit(
    api_key: APIKey = Depends(require_auth),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
) -> APIKey:
    """Dependency that checks concurrent jobs limit.

    Raises HTTPException 429 if too many concurrent jobs.
    """
    result = await rate_limiter.check_concurrent_jobs(api_key.tenant_id)
    if not result.allowed:
        headers = _build_rate_limit_headers(
            limit=result.limit,
            remaining=result.remaining,
            reset_seconds=result.reset_seconds,
        )
        raise HTTPException(
            status_code=429,
            detail=Err.CONCURRENT_JOB_LIMIT.format(limit=result.limit),
            headers=headers,
        )
    return api_key


async def check_concurrent_sessions_limit(
    api_key: APIKey = Depends(require_auth),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
) -> APIKey:
    """Dependency that checks concurrent sessions limit.

    Raises HTTPException 429 if too many concurrent sessions.
    """
    result = await rate_limiter.check_concurrent_sessions(api_key.tenant_id)
    if not result.allowed:
        headers = _build_rate_limit_headers(
            limit=result.limit,
            remaining=result.remaining,
            reset_seconds=result.reset_seconds,
        )
        raise HTTPException(
            status_code=429,
            detail=Err.CONCURRENT_SESSION_LIMIT.format(limit=result.limit),
            headers=headers,
        )
    return api_key


# =============================================================================
# Principal-based rate limited dependencies (M45)
# =============================================================================


async def get_principal_with_job_rate_limit(
    request: Request,
    principal: Principal = Depends(get_principal),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
) -> Principal:
    """Get authenticated Principal with job rate limit checks.

    Checks both request rate limit and concurrent jobs limit.
    Use with SecurityManager for permission checks.
    """
    # Check request rate limit
    rate_result = await rate_limiter.check_request_rate(principal.tenant_id)
    request.state.openai_rate_limit_headers = _build_rate_limit_headers(
        limit=rate_result.limit,
        remaining=rate_result.remaining,
        reset_seconds=rate_result.reset_seconds,
    )
    if not rate_result.allowed:
        headers = dict(request.state.openai_rate_limit_headers)
        headers["Retry-After"] = str(rate_result.reset_seconds)
        raise HTTPException(
            status_code=429,
            detail=Err.RATE_LIMIT_EXCEEDED,
            headers=headers,
        )

    # Check concurrent jobs limit
    jobs_result = await rate_limiter.check_concurrent_jobs(principal.tenant_id)
    if not jobs_result.allowed:
        headers = _build_rate_limit_headers(
            limit=jobs_result.limit,
            remaining=jobs_result.remaining,
            reset_seconds=jobs_result.reset_seconds,
        )
        raise HTTPException(
            status_code=429,
            detail=Err.CONCURRENT_JOB_LIMIT.format(limit=jobs_result.limit),
            headers=headers,
        )

    return principal
