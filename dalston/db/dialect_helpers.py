"""Thin dialect-aware query helpers for PostgreSQL + SQLite portability.

Each helper inspects the bound engine dialect at call time and emits the
correct SQL variant. Callers import from here instead of using
dialect-specific SQLAlchemy subpackages directly.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase


async def build_insert_or_ignore(
    db: AsyncSession,
    model_class: type[DeclarativeBase],
    values: dict[str, Any],
    *,
    returning: Any | None = None,
) -> Any | None:
    """Execute an INSERT ... ON CONFLICT DO NOTHING, returning a scalar or None.

    Args:
        db:          The active async session.
        model_class: The ORM model class (table target).
        values:      Row values dict.
        returning:   Optional column expression to return (e.g. ``Model.id``).

    Returns:
        The value of the ``returning`` expression for the inserted row, or
        ``None`` if the row was silently dropped due to a conflict.
    """
    dialect_name = db.get_bind().dialect.name
    base_stmt: Any

    if dialect_name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        base_stmt = pg_insert(model_class).values(**values).on_conflict_do_nothing()
    else:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        base_stmt = sqlite_insert(model_class).values(**values).on_conflict_do_nothing()

    if returning is not None:
        final_stmt = base_stmt.returning(returning)
        result = await db.execute(final_stmt)
        return result.scalar_one_or_none()

    await db.execute(base_stmt)
    return None


def apply_for_update_with_dialect(
    query: Select,
    dialect_name: str,
    *,
    skip_locked: bool = False,
) -> Select:
    """Apply FOR UPDATE only when the dialect supports it.

    Args:
        query:        A SQLAlchemy ``Select`` statement.
        dialect_name: The dialect name string (e.g. ``"postgresql"``).
        skip_locked:  If True, add ``SKIP LOCKED`` (Postgres only).

    Returns:
        The query, with ``with_for_update`` applied for Postgres; unchanged
        for SQLite.
    """
    if dialect_name == "postgresql":
        return query.with_for_update(skip_locked=skip_locked)
    return query
