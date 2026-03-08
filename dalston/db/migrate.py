"""Programmatic Alembic migration runner.

Provides ``upgrade_to_head()`` which is the single entry-point for schema
management at startup. It runs ``alembic upgrade head`` for both PostgreSQL
and SQLite, since the migration chain uses only dialect-portable DDL.

Typed exceptions replace generic failures so the startup pathway can surface
actionable remediation instructions.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import structlog
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

log = structlog.get_logger()

_SQLITE_MIN_VERSION = (3, 35, 0)  # Minimum for RETURNING support
_POSTGRES_MIGRATION_LOCK_KEY = 59059331

# ---------------------------------------------------------------------------
# Legacy migration chain support
# ---------------------------------------------------------------------------

# The revision ID of the consolidated squash migration.  Any database stamped
# at a legacy revision (0001–0038, see below) will be re-stamped here so that
# ``alembic upgrade head`` can proceed without traversing the old chain.
_SQUASH_REVISION = "squash_0038"

# Exhaustive set of revision IDs from the pre-squash migration chain.
# Includes every numeric revision (0001–0038) plus the non-numeric data
# migration that was inserted mid-chain.
_LEGACY_REVISIONS: frozenset[str] = frozenset(
    {f"{i:04d}" for i in range(1, 39)} | {"729dffbba68f"}
)


# ---------------------------------------------------------------------------
# Typed result / exceptions
# ---------------------------------------------------------------------------


@dataclass
class MigrationResult:
    """Summary of a completed migration run."""

    current_revision: str
    applied_count: int
    warnings: list[str] = field(default_factory=list)


class MigrationError(RuntimeError):
    """Base class for all migration errors."""


class MigrationLockError(MigrationError):
    """Another process holds the migration lock (WAL journal file present)."""


class MigrationCorruptError(MigrationError):
    """The Alembic revision table is in an unexpected state."""


class MigrationVersionError(MigrationError):
    """SQLite version is below the required minimum."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalize_url(database_url: str) -> str:
    """Promote plain scheme prefixes to async driver equivalents."""
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if database_url.startswith("sqlite://"):
        return database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return database_url


def _sqlite_db_path(database_url: str) -> Path | None:
    """Extract the filesystem path from a SQLite URL, or None for :memory:."""
    parsed = make_url(database_url)
    db_name = parsed.database
    if not db_name or db_name == ":memory:":
        return None
    db_path = Path(db_name).expanduser()
    if not db_path.is_absolute():
        db_path = Path.cwd() / db_path
    return db_path


def _check_sqlite_preflight(database_url: str) -> None:
    """Raise MigrationError subclasses for unrecoverable SQLite preconditions."""
    # --- Version check ---
    version_info = tuple(int(x) for x in sqlite3.sqlite_version.split("."))
    if version_info < _SQLITE_MIN_VERSION:
        needed = ".".join(str(v) for v in _SQLITE_MIN_VERSION)
        raise MigrationVersionError(
            f"SQLite {sqlite3.sqlite_version} is below minimum {needed}. "
            "Upgrade Python to 3.11+ or install a newer SQLite."
        )

    db_path = _sqlite_db_path(database_url)
    if db_path is not None:
        # Ensure parent directory exists so SQLite can create the file.
        db_path.parent.mkdir(parents=True, exist_ok=True)

        if db_path.exists() and not os.access(db_path, os.W_OK):
            raise MigrationError(f"SQLite database file is not writable: {db_path}")
        if db_path.exists():
            wal_path = db_path.with_suffix(db_path.suffix + "-wal")
            if wal_path.exists():
                # WAL files can legitimately survive a clean shutdown or crash
                # recovery; their mere existence is not a reliable lock signal.
                # Verify by attempting BEGIN EXCLUSIVE with timeout=0 — this
                # fails immediately only when another process truly holds the
                # database open.
                try:
                    with sqlite3.connect(str(db_path), timeout=0) as _conn:
                        _conn.execute("BEGIN EXCLUSIVE")
                        _conn.execute("ROLLBACK")
                except sqlite3.OperationalError as exc:
                    raise MigrationLockError(
                        f"SQLite WAL journal detected ({wal_path}) and an "
                        "exclusive lock could not be acquired — another "
                        "process is using the database. "
                        "Stop other processes and retry."
                    ) from exc


def _is_postgres_url(database_url: str) -> bool:
    """Return True when the URL targets PostgreSQL."""
    return make_url(database_url).drivername.startswith("postgresql")


async def _get_current_head(alembic_cfg) -> str:
    """Return the current head revision from Alembic script directory."""
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(alembic_cfg)
    heads = script.get_heads()
    if not heads:
        raise MigrationCorruptError("Alembic script directory has no head revisions.")
    if len(heads) > 1:
        raise MigrationCorruptError(
            f"Multiple Alembic heads found: {heads}. Run `alembic merge heads`."
        )
    return heads[0]


async def _get_current_revision(engine) -> str | None:
    """Return the current alembic_version value, or None if the DB is not yet stamped.

    Only swallows the "no such table / relation does not exist" error that
    occurs on a genuinely fresh database.  All other errors (connection
    failures, permission errors) are re-raised as MigrationError so the
    caller gets an actionable diagnosis instead of a silent None.
    """
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError, ProgrammingError

    async with engine.connect() as conn:
        try:
            result = await conn.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            )
            row = result.fetchone()
            return row[0] if row else None
        except (OperationalError, ProgrammingError) as exc:
            err = str(exc.orig or exc).lower()
            # Fresh database: alembic_version table hasn't been created yet.
            if "alembic_version" in err or "no such table" in err:
                return None
            log.debug("alembic_version_check_failed", error=str(exc))
            raise MigrationError(
                f"Could not determine current migration revision: {exc}"
            ) from exc


def _make_alembic_config(
    database_url: str,
):  # type annotation omitted: Config imported lazily
    """Build an Alembic Config pointed at this package's alembic.ini."""
    from alembic.config import Config

    # Locate alembic.ini relative to this file's package root
    here = Path(__file__).resolve()
    # Walk up to find the repo root (contains alembic.ini)
    repo_root = here
    for _ in range(10):
        repo_root = repo_root.parent
        if (repo_root / "alembic.ini").exists():
            break
    else:
        raise MigrationError("Could not locate alembic.ini in parent directories.")

    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    # Signal to env.py not to override the URL with the DATABASE_URL env var.
    cfg.attributes["_dalston_url_set"] = True
    return cfg


async def _run_with_postgres_migration_lock(
    database_url: str,
    operation,
):
    """Serialize PostgreSQL migrations across concurrent service startups."""
    lock_engine = create_async_engine(
        database_url,
        echo=False,
        isolation_level="AUTOCOMMIT",
    )
    try:
        async with lock_engine.connect() as connection:
            await connection.execute(
                text("SELECT pg_advisory_lock(:key)"),
                {"key": _POSTGRES_MIGRATION_LOCK_KEY},
            )
            try:
                return await operation()
            finally:
                await connection.execute(
                    text("SELECT pg_advisory_unlock(:key)"),
                    {"key": _POSTGRES_MIGRATION_LOCK_KEY},
                )
    finally:
        await lock_engine.dispose()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def upgrade_to_head(database_url: str) -> MigrationResult:
    """Apply all pending Alembic migrations up to the current head revision.

    The migration chain uses dialect-portable DDL, so the same ``alembic
    upgrade head`` path works for both PostgreSQL and SQLite.

    Args:
        database_url: Connection URL (plain or async-prefixed scheme).

    Returns:
        MigrationResult describing what was applied.

    Raises:
        MigrationVersionError: SQLite version too old.
        MigrationLockError:    SQLite WAL journal held by another process.
        MigrationCorruptError: Unexpected Alembic state.
        MigrationError:        Other unrecoverable migration failures.
    """
    url = _normalize_url(database_url)
    is_sqlite = "sqlite" in url

    if is_sqlite:
        _check_sqlite_preflight(url)

    alembic_cfg = _make_alembic_config(url)

    try:
        head = await _get_current_head(alembic_cfg)
        if _is_postgres_url(url):
            return await _run_with_postgres_migration_lock(
                url,
                lambda: _run_alembic_upgrade(alembic_cfg, url, head),
            )
        return await _run_alembic_upgrade(alembic_cfg, url, head)
    except (
        MigrationError,
        MigrationVersionError,
        MigrationLockError,
        MigrationCorruptError,
    ):
        raise
    except Exception as exc:
        raise MigrationError(f"Unexpected migration failure: {exc}") from exc


async def _run_alembic_upgrade(alembic_cfg, url: str, head: str) -> MigrationResult:
    """Run alembic upgrade head in a thread (Alembic is sync)."""
    engine = create_async_engine(url, echo=False)
    try:
        current = await _get_current_revision(engine)
        log.info("alembic_upgrade_start", current_revision=current, target=head)

        # Legacy-chain bridge: if the DB is stamped at any pre-squash revision
        # (0001–0038 or the data migration), re-stamp it at the squash head.
        # The schema is already correct because those revisions applied all the
        # same DDL incrementally; we just need Alembic's version table to agree
        # with the new consolidated chain before running ``upgrade head``.
        if current in _LEGACY_REVISIONS:
            log.info(
                "legacy_revision_detected_restamping",
                from_revision=current,
                to_revision=_SQUASH_REVISION,
            )
            await asyncio.to_thread(_stamp_sync, alembic_cfg, _SQUASH_REVISION)

        await asyncio.to_thread(_run_upgrade_sync, alembic_cfg)
        current_after = await _get_current_revision(engine)
    finally:
        await engine.dispose()

    applied = 0 if current == current_after else 1
    log.info("alembic_upgrade_complete", revision=current_after)
    return MigrationResult(
        current_revision=current_after or head,
        applied_count=applied,
    )


def _stamp_sync(cfg, revision: str) -> None:
    """Synchronous Alembic stamp (runs in thread)."""
    from alembic import command

    command.stamp(cfg, revision)


def _run_upgrade_sync(cfg) -> None:
    """Synchronous Alembic upgrade (runs in thread)."""
    from alembic import command

    command.upgrade(cfg, "head")
