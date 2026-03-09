from __future__ import annotations

import pytest

from dalston.db import migrate


class _FakeConnection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, int]]] = []

    async def execute(self, statement, params):
        self.calls.append((str(statement), params))


class _FakeConnectionContext:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    async def __aenter__(self) -> _FakeConnection:
        return self._connection

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _FakeEngine:
    def __init__(self) -> None:
        self.connection = _FakeConnection()
        self.disposed = False

    def connect(self) -> _FakeConnectionContext:
        return _FakeConnectionContext(self.connection)

    async def dispose(self) -> None:
        self.disposed = True


@pytest.mark.asyncio
async def test_upgrade_to_head_serializes_postgres_migrations(monkeypatch):
    fake_engine = _FakeEngine()
    seen: dict[str, object] = {}

    monkeypatch.setattr(migrate, "_make_alembic_config", lambda url: object())
    monkeypatch.setattr(migrate, "_get_current_head", lambda cfg: _async_value("head"))
    monkeypatch.setattr(
        migrate,
        "create_async_engine",
        lambda *args, **kwargs: _capture_engine(seen, fake_engine, kwargs),
    )
    monkeypatch.setattr(
        migrate,
        "_run_alembic_upgrade",
        lambda cfg, url, head: _async_value(
            migrate.MigrationResult(current_revision=head, applied_count=1)
        ),
    )

    result = await migrate.upgrade_to_head("postgresql://user:pass@localhost/dalston")

    assert result.current_revision == "head"
    assert result.applied_count == 1
    assert seen["kwargs"]["isolation_level"] == "AUTOCOMMIT"
    assert fake_engine.disposed is True
    assert fake_engine.connection.calls == [
        (
            "SELECT pg_advisory_lock(:key)",
            {"key": migrate._POSTGRES_MIGRATION_LOCK_KEY},
        ),
        (
            "SELECT pg_advisory_unlock(:key)",
            {"key": migrate._POSTGRES_MIGRATION_LOCK_KEY},
        ),
    ]


@pytest.mark.asyncio
async def test_upgrade_to_head_skips_postgres_lock_for_sqlite(monkeypatch):
    monkeypatch.setattr(migrate, "_check_sqlite_preflight", lambda url: None)
    monkeypatch.setattr(migrate, "_make_alembic_config", lambda url: object())
    monkeypatch.setattr(migrate, "_get_current_head", lambda cfg: _async_value("head"))
    monkeypatch.setattr(
        migrate,
        "_run_alembic_upgrade",
        lambda cfg, url, head: _async_value(
            migrate.MigrationResult(current_revision=head, applied_count=0)
        ),
    )

    def _unexpected_engine(*args, **kwargs):
        raise AssertionError("PostgreSQL advisory lock should not run for SQLite URLs")

    monkeypatch.setattr(migrate, "create_async_engine", _unexpected_engine)

    result = await migrate.upgrade_to_head("sqlite+aiosqlite:///tmp/dalston.db")

    assert result.current_revision == "head"
    assert result.applied_count == 0


async def _async_value(value):
    return value


def _capture_engine(store: dict[str, object], engine: _FakeEngine, kwargs):
    store["kwargs"] = kwargs
    return engine
