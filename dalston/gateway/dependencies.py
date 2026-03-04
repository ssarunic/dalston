"""FastAPI dependency injection functions."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

import structlog
from fastapi import Depends, HTTPException, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.common.redis import get_redis as _get_redis_client
from dalston.config import Settings
from dalston.config import get_settings as _get_settings
from dalston.db.session import async_session
from dalston.gateway.middleware.auth import authenticate_request
from dalston.gateway.security.manager import SecurityManager
from dalston.gateway.security.manager import (
    get_security_manager as _get_security_manager,
)
from dalston.gateway.security.principal import Principal
from dalston.gateway.services.auth import APIKey, AuthService, Scope
from dalston.gateway.services.export import ExportService
from dalston.gateway.services.ingestion import AudioIngestionService
from dalston.gateway.services.jobs import JobsService
from dalston.gateway.services.rate_limiter import RedisRateLimiter

if TYPE_CHECKING:
    from dalston.common.audit import AuditService
    from dalston.session_router import SessionRouter

logger = structlog.get_logger()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async database session."""
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()


async def get_redis() -> Redis:
    """Get async Redis client."""
    return await _get_redis_client()


def get_settings() -> Settings:
    """Get application settings."""
    return _get_settings()


# Service singletons for dependency injection
_jobs_service: JobsService | None = None
_export_service: ExportService | None = None
_audit_service: AuditService | None = None


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


def get_session_router() -> SessionRouter:
    """Get SessionRouter instance.

    The router is initialized in main.py lifespan and stored globally.
    """
    from dalston.gateway.main import session_router

    if session_router is None:
        raise HTTPException(
            status_code=503,
            detail="Session router not initialized",
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
        prefix="dk_dev00000",
        name="Development Key",
        tenant_id=DEFAULT_TENANT_ID,
        scopes=[Scope.ADMIN],  # Full access in dev mode
        rate_limit=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        last_used_at=None,
        expires_at=datetime(2099, 12, 31, 23, 59, 59, tzinfo=UTC),
        revoked_at=None,
    )


async def require_auth(
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
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
        request.state.api_key = dev_key
        request.state.tenant_id = dev_key.tenant_id
        return dev_key

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


def set_rate_limiter(limiter: RedisRateLimiter | None) -> None:
    """Set the rate limiter instance (for testing)."""
    global _rate_limiter
    _rate_limiter = limiter


def reset_rate_limiter() -> None:
    """Reset the rate limiter singleton (for testing)."""
    global _rate_limiter
    _rate_limiter = None


async def get_rate_limiter(
    redis: Redis = Depends(get_redis),
    settings: Settings = Depends(get_settings),
    db: AsyncSession = Depends(get_db),
) -> RedisRateLimiter:
    """Get RateLimiter instance.

    Creates a singleton rate limiter with Redis backend.
    On each request, limits are refreshed from DB overrides (5s TTL cache).
    """
    global _rate_limiter
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
    except Exception:
        # If settings service fails, keep existing limits
        logger.warning("failed_to_refresh_rate_limits", exc_info=True)

    return _rate_limiter


async def check_request_rate_limit(
    api_key: APIKey = Depends(require_auth),
    rate_limiter: RedisRateLimiter = Depends(get_rate_limiter),
) -> APIKey:
    """Dependency that checks request rate limit.

    Raises HTTPException 429 if rate limit exceeded.
    """
    result = await rate_limiter.check_request_rate(api_key.tenant_id)
    if not result.allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={
                "Retry-After": str(result.reset_seconds),
                "X-RateLimit-Limit": str(result.limit),
                "X-RateLimit-Remaining": str(result.remaining),
            },
        )
    return api_key


async def check_concurrent_jobs_limit(
    api_key: APIKey = Depends(require_auth),
    rate_limiter: RedisRateLimiter = Depends(get_rate_limiter),
) -> APIKey:
    """Dependency that checks concurrent jobs limit.

    Raises HTTPException 429 if too many concurrent jobs.
    """
    result = await rate_limiter.check_concurrent_jobs(api_key.tenant_id)
    if not result.allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Concurrent job limit exceeded ({result.limit} max)",
            headers={
                "X-RateLimit-Limit": str(result.limit),
                "X-RateLimit-Remaining": str(result.remaining),
            },
        )
    return api_key


async def check_concurrent_sessions_limit(
    api_key: APIKey = Depends(require_auth),
    rate_limiter: RedisRateLimiter = Depends(get_rate_limiter),
) -> APIKey:
    """Dependency that checks concurrent sessions limit.

    Raises HTTPException 429 if too many concurrent sessions.
    """
    result = await rate_limiter.check_concurrent_sessions(api_key.tenant_id)
    if not result.allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Concurrent session limit exceeded ({result.limit} max)",
            headers={
                "X-RateLimit-Limit": str(result.limit),
                "X-RateLimit-Remaining": str(result.remaining),
            },
        )
    return api_key


# =============================================================================
# Principal-based rate limited dependencies (M45)
# =============================================================================


async def get_principal_with_job_rate_limit(
    principal: Principal = Depends(get_principal),
    rate_limiter: RedisRateLimiter = Depends(get_rate_limiter),
) -> Principal:
    """Get authenticated Principal with job rate limit checks.

    Checks both request rate limit and concurrent jobs limit.
    Use with SecurityManager for permission checks.
    """
    # Check request rate limit
    rate_result = await rate_limiter.check_request_rate(principal.tenant_id)
    if not rate_result.allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={
                "Retry-After": str(rate_result.reset_seconds),
                "X-RateLimit-Limit": str(rate_result.limit),
                "X-RateLimit-Remaining": str(rate_result.remaining),
            },
        )

    # Check concurrent jobs limit
    jobs_result = await rate_limiter.check_concurrent_jobs(principal.tenant_id)
    if not jobs_result.allowed:
        raise HTTPException(
            status_code=429,
            detail=f"Concurrent job limit exceeded ({jobs_result.limit} max)",
            headers={
                "X-RateLimit-Limit": str(jobs_result.limit),
                "X-RateLimit-Remaining": str(jobs_result.remaining),
            },
        )

    return principal
