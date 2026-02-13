"""FastAPI dependency injection functions."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, HTTPException, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.common.redis import get_redis as _get_redis_client
from dalston.config import Settings
from dalston.config import get_settings as _get_settings
from dalston.db.session import async_session
from dalston.gateway.middleware.auth import authenticate_request
from dalston.gateway.middleware.auth import require_scope as _require_scope
from dalston.gateway.services.auth import APIKey, AuthService, Scope
from dalston.gateway.services.export import ExportService
from dalston.gateway.services.jobs import JobsService
from dalston.gateway.services.rate_limiter import RedisRateLimiter
from dalston.gateway.services.retention import RetentionService

if TYPE_CHECKING:
    from dalston.common.audit import AuditService
    from dalston.session_router import SessionRouter


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
_retention_service: RetentionService | None = None
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


def get_retention_service() -> RetentionService:
    """Get RetentionService instance (singleton)."""
    global _retention_service
    if _retention_service is None:
        _retention_service = RetentionService()
    return _retention_service


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


async def require_auth(
    request: Request,
    auth_service: AuthService = Depends(get_auth_service),
) -> APIKey:
    """Dependency that requires a valid API key.

    Extracts and validates the API key from the request.
    Attaches api_key and tenant_id to request.state.

    Returns:
        Validated APIKey object

    Raises:
        HTTPException 401: If key is missing or invalid
        HTTPException 429: If rate limit exceeded
    """
    return await authenticate_request(request, auth_service)


def require_scope_dependency(scope: Scope) -> Callable:
    """Factory for scope-checking dependencies.

    Usage:
        @router.post("/endpoint")
        async def endpoint(
            api_key: APIKey = Depends(require_scope_dependency(Scope.JOBS_WRITE))
        ):
            ...

    Args:
        scope: Required scope for the endpoint

    Returns:
        Dependency function that validates the scope
    """

    async def check_scope(
        api_key: APIKey = Depends(require_auth),
    ) -> APIKey:
        _require_scope(api_key, scope)
        return api_key

    return check_scope


# Pre-built scope dependencies for common use cases
RequireJobsRead = Annotated[APIKey, Depends(require_scope_dependency(Scope.JOBS_READ))]
RequireJobsWrite = Annotated[
    APIKey, Depends(require_scope_dependency(Scope.JOBS_WRITE))
]
RequireRealtime = Annotated[APIKey, Depends(require_scope_dependency(Scope.REALTIME))]
RequireWebhooks = Annotated[APIKey, Depends(require_scope_dependency(Scope.WEBHOOKS))]
RequireAdmin = Annotated[APIKey, Depends(require_scope_dependency(Scope.ADMIN))]


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
) -> RedisRateLimiter:
    """Get RateLimiter instance.

    Creates a singleton rate limiter with Redis backend.
    """
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RedisRateLimiter(
            redis=redis,
            requests_per_minute=settings.rate_limit_requests_per_minute,
            max_concurrent_jobs=settings.rate_limit_concurrent_jobs,
            max_concurrent_sessions=settings.rate_limit_concurrent_sessions,
        )
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


def require_jobs_write_rate_limited_dependency() -> Callable:
    """Dependency that requires JOBS_WRITE scope and checks rate limits."""

    async def check(
        api_key: APIKey = Depends(require_scope_dependency(Scope.JOBS_WRITE)),
        rate_limiter: RedisRateLimiter = Depends(get_rate_limiter),
    ) -> APIKey:
        # Check request rate limit
        rate_result = await rate_limiter.check_request_rate(api_key.tenant_id)
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
        jobs_result = await rate_limiter.check_concurrent_jobs(api_key.tenant_id)
        if not jobs_result.allowed:
            raise HTTPException(
                status_code=429,
                detail=f"Concurrent job limit exceeded ({jobs_result.limit} max)",
                headers={
                    "X-RateLimit-Limit": str(jobs_result.limit),
                    "X-RateLimit-Remaining": str(jobs_result.remaining),
                },
            )

        return api_key

    return check


def require_realtime_rate_limited_dependency() -> Callable:
    """Dependency that requires REALTIME scope and checks rate limits."""

    async def check(
        api_key: APIKey = Depends(require_scope_dependency(Scope.REALTIME)),
        rate_limiter: RedisRateLimiter = Depends(get_rate_limiter),
    ) -> APIKey:
        # Check request rate limit
        rate_result = await rate_limiter.check_request_rate(api_key.tenant_id)
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

        # Check concurrent sessions limit
        sessions_result = await rate_limiter.check_concurrent_sessions(
            api_key.tenant_id
        )
        if not sessions_result.allowed:
            raise HTTPException(
                status_code=429,
                detail=f"Concurrent session limit exceeded ({sessions_result.limit} max)",
                headers={
                    "X-RateLimit-Limit": str(sessions_result.limit),
                    "X-RateLimit-Remaining": str(sessions_result.remaining),
                },
            )

        return api_key

    return check


# Rate-limited scope dependencies
RequireJobsWriteRateLimited = Annotated[
    APIKey, Depends(require_jobs_write_rate_limited_dependency())
]
RequireRealtimeRateLimited = Annotated[
    APIKey, Depends(require_realtime_rate_limited_dependency())
]
