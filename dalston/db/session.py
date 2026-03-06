"""Async SQLAlchemy session factory with mode-aware lazy initialization."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from dalston.config import get_settings
from dalston.db.models import Base

# Default tenant for M01 (no auth)
DEFAULT_TENANT_ID = UUID("00000000-0000-0000-0000-000000000000")
DEFAULT_TENANT_NAME = "default"

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_mode: str | None = None


class _EngineProxy:
    """Lazy proxy that preserves `engine` import compatibility."""

    def _get(self) -> AsyncEngine:
        return get_engine()

    async def dispose(self) -> None:
        await self._get().dispose()


engine = _EngineProxy()


def _ensure_sqlite_parent_dir(database_url: str) -> None:
    """Create parent directory for SQLite file-backed URLs if needed."""
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite"):
        return

    db_name = url.database
    if not db_name or db_name == ":memory:" or db_name.startswith("file:"):
        return

    db_path = Path(db_name).expanduser()
    if not db_path.is_absolute():
        db_path = Path.cwd() / db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)


def _build_engine() -> tuple[AsyncEngine, str]:
    settings = get_settings()
    mode = settings.runtime_mode
    database_url = (
        settings.database_url if mode == "distributed" else settings.lite_database_url
    )
    _ensure_sqlite_parent_dir(database_url)
    created_engine = create_async_engine(
        database_url,
        echo=False,
        pool_pre_ping=mode == "distributed",
    )
    return created_engine, mode


def get_engine() -> AsyncEngine:
    """Get lazily-initialized async engine for current runtime mode."""
    global _engine, _mode
    if _engine is None:
        _engine, _mode = _build_engine()
    return _engine


def _get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


def async_session() -> AsyncSession:
    """Backwards-compatible session factory callable."""
    return _get_session_factory()()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async database session."""
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db() -> None:
    """Initialize mode-specific database schema and defaults."""
    settings = get_settings()
    if settings.runtime_mode == "lite":
        await _init_lite_schema()
    else:
        async with get_engine().begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    await _ensure_default_tenant()


async def _init_lite_schema() -> None:
    """Bootstrap lite schema subset for batch path without Postgres-specific features."""
    statements = [
        """
        CREATE TABLE IF NOT EXISTS tenants (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            settings TEXT NOT NULL DEFAULT '{}',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            status TEXT NOT NULL,
            audio_uri TEXT NOT NULL,
            parameters TEXT NOT NULL DEFAULT '{}',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT,
            error TEXT,
            FOREIGN KEY(tenant_id) REFERENCES tenants(id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            status TEXT NOT NULL,
            runtime TEXT NOT NULL,
            config TEXT NOT NULL DEFAULT '{}',
            output_uri TEXT,
            FOREIGN KEY(job_id) REFERENCES jobs(id)
        )
        """,
    ]
    async with get_engine().begin() as conn:
        for stmt in statements:
            await conn.execute(text(stmt))


async def _ensure_default_tenant() -> None:
    """Ensure default tenant exists in both distributed and lite modes."""
    async with get_engine().begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO tenants (id, name)
                VALUES (:id, :name)
                ON CONFLICT(id) DO NOTHING
                """
            ),
            {"id": str(DEFAULT_TENANT_ID), "name": DEFAULT_TENANT_NAME},
        )


def reset_session_state() -> None:
    """Testing utility for resetting lazy globals."""
    global _engine, _session_factory, _mode
    _engine = None
    _session_factory = None
    _mode = None
