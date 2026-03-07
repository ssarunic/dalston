"""Alembic migration environment configuration.

Supports both PostgreSQL (asyncpg driver) and SQLite (aiosqlite driver).
The DATABASE_URL environment variable selects the dialect; plain scheme
prefixes are automatically promoted to their async equivalents.

SQLite notes:
- Minimum version 3.35.0 required for RETURNING support (Python 3.11+ ships 3.39+).
- render_as_batch=True enables ALTER TABLE emulation for SQLite, which does not
  support ADD/DROP COLUMN directly on existing tables.
"""

import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Import models for autogenerate support
from dalston.db.models import Base

# Alembic Config object
config = context.config

# Setup logging from alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Model metadata for autogenerate
target_metadata = Base.metadata

# ---------------------------------------------------------------------------
# URL normalisation: promote plain scheme → async driver
# ---------------------------------------------------------------------------
# Only apply the DATABASE_URL env-var when running the alembic CLI directly.
# When migrate.py calls command.upgrade() programmatically it sets the URL via
# cfg.set_main_option() and marks cfg.attributes["_dalston_url_set"] = True so
# we know NOT to override it here.
if not config.attributes.get("_dalston_url_set"):
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        if database_url.startswith("postgresql://"):
            database_url = database_url.replace(
                "postgresql://", "postgresql+asyncpg://", 1
            )
        elif database_url.startswith("sqlite://"):
            database_url = database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
        config.set_main_option("sqlalchemy.url", database_url)

# Detect the configured dialect so we can set render_as_batch for SQLite.
_configured_url: str = config.get_main_option("sqlalchemy.url") or ""
_is_sqlite = "sqlite" in _configured_url


def _make_context_kwargs() -> dict:
    """Return context.configure() kwargs appropriate for the active dialect."""
    kwargs: dict = {"target_metadata": target_metadata}
    if _is_sqlite:
        # SQLite requires batch mode to emulate ALTER TABLE operations.
        kwargs["render_as_batch"] = True
    return kwargs


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL and not an Engine.
    Calls to context.execute() emit the given string to the script output.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_make_context_kwargs(),
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations with the given connection."""
    context.configure(connection=connection, **_make_context_kwargs())

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
