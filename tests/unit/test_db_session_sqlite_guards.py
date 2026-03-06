from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from dalston.db import session as session_module


@pytest.mark.asyncio
async def test_sqlite_table_columns_rejects_unknown_table() -> None:
    conn = AsyncMock()

    with pytest.raises(ValueError, match="Unsupported SQLite bootstrap table"):
        await session_module._sqlite_table_columns(conn, "jobs;DROP TABLE jobs")

    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_sqlite_columns_rejects_unsafe_column_identifier() -> None:
    conn = AsyncMock()
    conn.execute.return_value.fetchall.return_value = []

    with pytest.raises(ValueError, match="Unsafe SQLite column identifier"):
        await session_module._ensure_sqlite_columns(
            conn,
            "jobs",
            {"bad-name": "TEXT"},
        )


@pytest.mark.asyncio
async def test_ensure_sqlite_columns_rejects_unsafe_column_ddl() -> None:
    conn = AsyncMock()
    conn.execute.return_value.fetchall.return_value = []

    with pytest.raises(ValueError, match="Unsafe SQLite DDL"):
        await session_module._ensure_sqlite_columns(
            conn,
            "jobs",
            {"safe_column": "TEXT; DROP TABLE jobs"},
        )
