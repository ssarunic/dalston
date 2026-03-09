from __future__ import annotations

from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from sqlalchemy.exc import IntegrityError
from starlette.requests import Request

from dalston.db.models import APIKeyModel
from dalston.gateway import dependencies as deps


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _SessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
        }
    )


@pytest.mark.asyncio
async def test_require_auth_none_creates_dev_key_record(monkeypatch) -> None:
    db = AsyncMock()
    db.add = Mock()
    db.execute.return_value = _ScalarResult(None)
    monkeypatch.setattr(deps, "async_session", lambda: _SessionContext(db))
    monkeypatch.setattr(
        deps,
        "_get_security_manager",
        lambda: type("SM", (), {"mode": "none"})(),
    )
    monkeypatch.setattr(
        deps,
        "_get_settings",
        lambda: type("Settings", (), {"runtime_mode": "distributed"})(),
    )

    api_key = await deps.require_auth(request=_request())

    assert api_key.id == UUID("00000000-0000-0000-0000-000000000002")
    db.add.assert_called_once()
    added = db.add.call_args.args[0]
    assert isinstance(added, APIKeyModel)
    assert added.id == api_key.id
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_require_auth_none_skips_dev_key_insert_when_present(monkeypatch) -> None:
    db = AsyncMock()
    db.add = Mock()
    db.execute.return_value = _ScalarResult("present")
    monkeypatch.setattr(deps, "async_session", lambda: _SessionContext(db))
    monkeypatch.setattr(
        deps,
        "_get_security_manager",
        lambda: type("SM", (), {"mode": "none"})(),
    )
    monkeypatch.setattr(
        deps,
        "_get_settings",
        lambda: type("Settings", (), {"runtime_mode": "distributed"})(),
    )

    await deps.require_auth(request=_request())

    db.add.assert_not_called()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_require_auth_none_skips_dev_key_insert_in_lite_mode(monkeypatch) -> None:
    db = AsyncMock()
    db.add = Mock()
    async_session = Mock(return_value=_SessionContext(db))
    monkeypatch.setattr(deps, "async_session", async_session)
    monkeypatch.setattr(
        deps,
        "_get_security_manager",
        lambda: type("SM", (), {"mode": "none"})(),
    )
    monkeypatch.setattr(
        deps,
        "_get_settings",
        lambda: type("Settings", (), {"runtime_mode": "lite"})(),
    )

    await deps.require_auth(request=_request())

    async_session.assert_not_called()
    db.execute.assert_not_awaited()
    db.add.assert_not_called()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_dev_key_record_handles_insert_race() -> None:
    db = AsyncMock()
    db.add = Mock()
    db.execute.return_value = _ScalarResult(None)
    db.commit.side_effect = IntegrityError("INSERT", {}, Exception("dup"))

    await deps._ensure_dev_api_key_record(db, deps._get_dev_api_key())

    db.rollback.assert_awaited_once()
