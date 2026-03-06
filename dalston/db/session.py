"""Async SQLAlchemy session factory with mode-aware lazy initialization."""

from __future__ import annotations

import re
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

_SQLITE_BOOTSTRAP_TABLES = frozenset({"jobs", "tasks", "models", "api_keys"})
_SQLITE_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SQLITE_COLUMN_DDL_RE = re.compile(r"^[A-Za-z0-9_ (),.'\[\]{}+-]+$")


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


async def _sqlite_table_columns(conn, table_name: str) -> set[str]:
    if table_name not in _SQLITE_BOOTSTRAP_TABLES:
        raise ValueError(f"Unsupported SQLite bootstrap table: {table_name}")
    result = await conn.execute(text(f'PRAGMA table_info("{table_name}")'))
    return {str(row[1]) for row in result.fetchall()}


async def _ensure_sqlite_columns(
    conn,
    table_name: str,
    required_columns: dict[str, str],
) -> None:
    if table_name not in _SQLITE_BOOTSTRAP_TABLES:
        raise ValueError(f"Unsupported SQLite bootstrap table: {table_name}")

    existing = await _sqlite_table_columns(conn, table_name)
    for column_name, column_ddl in required_columns.items():
        if not _SQLITE_IDENTIFIER_RE.fullmatch(column_name):
            raise ValueError(f"Unsafe SQLite column identifier: {column_name}")
        if not _SQLITE_COLUMN_DDL_RE.fullmatch(column_ddl):
            raise ValueError(
                f"Unsafe SQLite DDL for column {column_name}: {column_ddl}"
            )
        if column_name not in existing:
            await conn.execute(
                text(
                    f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" {column_ddl}'
                )
            )


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
    """Bootstrap lite schema compatible with current ORM-backed gateway paths."""
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
            display_name TEXT NOT NULL DEFAULT '',
            audio_uri TEXT NOT NULL,
            audio_format TEXT,
            audio_duration REAL,
            audio_sample_rate INTEGER,
            audio_channels INTEGER,
            audio_bit_depth INTEGER,
            parameters TEXT NOT NULL DEFAULT '{}',
            started_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT,
            retention INTEGER NOT NULL DEFAULT 30,
            purge_after TEXT,
            purged_at TEXT,
            result_language_code TEXT,
            result_word_count INTEGER,
            result_segment_count INTEGER,
            result_speaker_count INTEGER,
            result_character_count INTEGER,
            pii_detection_enabled INTEGER NOT NULL DEFAULT 0,
            pii_entity_types TEXT,
            pii_redact_audio INTEGER NOT NULL DEFAULT 0,
            pii_redaction_mode TEXT,
            pii_entities_detected INTEGER,
            pii_redacted_audio_uri TEXT,
            created_by_key_id TEXT,
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
            dependencies TEXT NOT NULL DEFAULT '[]',
            config TEXT NOT NULL DEFAULT '{}',
            input_uri TEXT,
            output_uri TEXT,
            retries INTEGER NOT NULL DEFAULT 0,
            max_retries INTEGER NOT NULL DEFAULT 2,
            required INTEGER NOT NULL DEFAULT 1,
            error TEXT,
            started_at TEXT,
            completed_at TEXT,
            FOREIGN KEY(job_id) REFERENCES jobs(id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS models (
            id TEXT PRIMARY KEY,
            name TEXT,
            runtime TEXT NOT NULL,
            runtime_model_id TEXT NOT NULL,
            stage TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'not_downloaded',
            download_path TEXT,
            size_bytes INTEGER,
            downloaded_at TEXT,
            source TEXT,
            library_name TEXT,
            languages TEXT,
            word_timestamps INTEGER NOT NULL DEFAULT 0,
            punctuation INTEGER NOT NULL DEFAULT 0,
            capitalization INTEGER NOT NULL DEFAULT 0,
            streaming INTEGER NOT NULL DEFAULT 0,
            min_vram_gb REAL,
            min_ram_gb REAL,
            supports_cpu INTEGER NOT NULL DEFAULT 1,
            model_metadata TEXT NOT NULL DEFAULT '{}',
            metadata_source TEXT NOT NULL DEFAULT 'yaml',
            last_used_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS api_keys (
            id TEXT PRIMARY KEY,
            key_hash TEXT NOT NULL UNIQUE,
            prefix TEXT NOT NULL,
            name TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            scopes TEXT NOT NULL,
            rate_limit INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_used_at TEXT,
            expires_at TEXT,
            revoked_at TEXT,
            created_by_key_id TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS ix_jobs_tenant_id ON jobs (tenant_id)",
        "CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs (status)",
        "CREATE INDEX IF NOT EXISTS ix_jobs_created_at ON jobs (created_at)",
        "CREATE INDEX IF NOT EXISTS ix_tasks_job_id ON tasks (job_id)",
        "CREATE INDEX IF NOT EXISTS ix_tasks_stage ON tasks (stage)",
        "CREATE INDEX IF NOT EXISTS ix_tasks_status ON tasks (status)",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_tasks_job_id_stage ON tasks (job_id, stage)",
        "CREATE INDEX IF NOT EXISTS ix_models_runtime ON models (runtime)",
        "CREATE INDEX IF NOT EXISTS ix_models_stage ON models (stage)",
        "CREATE INDEX IF NOT EXISTS ix_models_status ON models (status)",
    ]

    jobs_required_columns = {
        "display_name": "TEXT NOT NULL DEFAULT ''",
        "audio_format": "TEXT",
        "audio_duration": "REAL",
        "audio_sample_rate": "INTEGER",
        "audio_channels": "INTEGER",
        "audio_bit_depth": "INTEGER",
        "started_at": "TEXT",
        "retention": "INTEGER NOT NULL DEFAULT 30",
        "purge_after": "TEXT",
        "purged_at": "TEXT",
        "result_language_code": "TEXT",
        "result_word_count": "INTEGER",
        "result_segment_count": "INTEGER",
        "result_speaker_count": "INTEGER",
        "result_character_count": "INTEGER",
        "pii_detection_enabled": "INTEGER NOT NULL DEFAULT 0",
        "pii_entity_types": "TEXT",
        "pii_redact_audio": "INTEGER NOT NULL DEFAULT 0",
        "pii_redaction_mode": "TEXT",
        "pii_entities_detected": "INTEGER",
        "pii_redacted_audio_uri": "TEXT",
        "created_by_key_id": "TEXT",
    }

    tasks_required_columns = {
        "dependencies": "TEXT NOT NULL DEFAULT '[]'",
        "input_uri": "TEXT",
        "retries": "INTEGER NOT NULL DEFAULT 0",
        "max_retries": "INTEGER NOT NULL DEFAULT 2",
        "required": "INTEGER NOT NULL DEFAULT 1",
        "error": "TEXT",
        "started_at": "TEXT",
        "completed_at": "TEXT",
    }

    models_required_columns = {
        "name": "TEXT",
        "runtime": "TEXT NOT NULL DEFAULT ''",
        "runtime_model_id": "TEXT NOT NULL DEFAULT ''",
        "stage": "TEXT NOT NULL DEFAULT ''",
        "status": "TEXT NOT NULL DEFAULT 'not_downloaded'",
        "download_path": "TEXT",
        "size_bytes": "INTEGER",
        "downloaded_at": "TEXT",
        "source": "TEXT",
        "library_name": "TEXT",
        "languages": "TEXT",
        "word_timestamps": "INTEGER NOT NULL DEFAULT 0",
        "punctuation": "INTEGER NOT NULL DEFAULT 0",
        "capitalization": "INTEGER NOT NULL DEFAULT 0",
        "streaming": "INTEGER NOT NULL DEFAULT 0",
        "min_vram_gb": "REAL",
        "min_ram_gb": "REAL",
        "supports_cpu": "INTEGER NOT NULL DEFAULT 1",
        "model_metadata": "TEXT NOT NULL DEFAULT '{}'",
        "metadata_source": "TEXT NOT NULL DEFAULT 'yaml'",
        "last_used_at": "TEXT",
        "created_at": "TEXT DEFAULT CURRENT_TIMESTAMP",
        "updated_at": "TEXT DEFAULT CURRENT_TIMESTAMP",
    }

    api_keys_required_columns = {
        "key_hash": "TEXT NOT NULL DEFAULT ''",
        "prefix": "TEXT NOT NULL DEFAULT ''",
        "name": "TEXT NOT NULL DEFAULT ''",
        "tenant_id": "TEXT NOT NULL DEFAULT ''",
        "scopes": "TEXT NOT NULL DEFAULT ''",
        "rate_limit": "INTEGER",
        "created_at": "TEXT DEFAULT CURRENT_TIMESTAMP",
        "last_used_at": "TEXT",
        "expires_at": "TEXT",
        "revoked_at": "TEXT",
        "created_by_key_id": "TEXT",
    }

    async with get_engine().begin() as conn:
        for stmt in statements:
            await conn.execute(text(stmt))
        await _ensure_sqlite_columns(conn, "jobs", jobs_required_columns)
        await _ensure_sqlite_columns(conn, "tasks", tasks_required_columns)
        await _ensure_sqlite_columns(conn, "models", models_required_columns)
        await _ensure_sqlite_columns(conn, "api_keys", api_keys_required_columns)


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
