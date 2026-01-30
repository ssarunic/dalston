"""FastAPI dependency injection functions."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from fastapi import HTTPException
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.common.redis import get_redis as _get_redis_client
from dalston.config import Settings, get_settings as _get_settings
from dalston.db.session import async_session
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


def get_session_router() -> "SessionRouter":
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
