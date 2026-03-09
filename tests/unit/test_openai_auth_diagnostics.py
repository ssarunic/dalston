"""Unit tests for targeted OpenAI-key diagnostics in auth middleware."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from dalston.gateway.middleware.auth import (
    WS_CLOSE_INVALID_KEY,
    authenticate_request,
    authenticate_websocket,
)


@pytest.mark.asyncio
async def test_authenticate_request_returns_targeted_openai_key_error() -> None:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/v1/audio/transcriptions",
        "headers": [(b"authorization", b"Bearer sk-test-key")],
    }
    request = Request(scope)
    auth_service = AsyncMock()

    with pytest.raises(HTTPException) as exc_info:
        await authenticate_request(request, auth_service)

    exc = exc_info.value
    assert exc.status_code == 401
    assert exc.detail["error"]["type"] == "authentication_error"
    assert exc.detail["error"]["code"] == "invalid_api_key"
    assert "OpenAI API key" in exc.detail["error"]["message"]
    auth_service.validate_api_key.assert_not_called()


@pytest.mark.asyncio
async def test_authenticate_websocket_rejects_sk_key_with_diagnostic() -> None:
    websocket = SimpleNamespace(
        headers={"authorization": "Bearer sk-test-key"},
        query_params={},
        url=SimpleNamespace(path="/v1/realtime"),
        close=AsyncMock(),
    )
    auth_service = AsyncMock()

    security_manager = SimpleNamespace(mode="rbac")
    with patch(
        "dalston.gateway.security.manager.get_security_manager",
        return_value=security_manager,
    ):
        result = await authenticate_websocket(websocket, auth_service)

    assert result is None
    websocket.close.assert_awaited_once()
    close_kwargs = websocket.close.await_args.kwargs
    assert close_kwargs["code"] == WS_CLOSE_INVALID_KEY
    assert "OpenAI API key" in close_kwargs["reason"]
    auth_service.validate_api_key.assert_not_called()
