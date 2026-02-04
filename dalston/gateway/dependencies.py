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

if TYPE_CHECKING:
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
    redis: Redis = Depends(get_redis),
) -> AuthService:
    """Get AuthService instance.

    Creates a new AuthService per request with the current Redis connection.
    AuthService is lightweight (no state beyond redis reference), so this
    avoids race conditions in the singleton pattern.
    """
    return AuthService(redis)


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
