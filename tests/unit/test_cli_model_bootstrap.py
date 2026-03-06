from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from dalston_cli.bootstrap.model_manager import (
    ModelBootstrapError,
    ensure_model_ready,
    read_model_status,
    resolve_bootstrap_model,
)


@dataclass
class _FakeResponse:
    status_code: int
    payload: dict[str, Any]

    def json(self) -> dict[str, Any]:
        return self.payload


class _FakeClient:
    def __init__(
        self,
        get_responses: list[_FakeResponse],
        post_responses: list[_FakeResponse] | None = None,
    ):
        self.get_responses = get_responses
        self.post_responses = post_responses or [_FakeResponse(200, {})]
        self.post_calls = 0

    def get(self, *_args, **_kwargs):
        return self.get_responses.pop(0)

    def post(self, *_args, **_kwargs):
        self.post_calls += 1
        return self.post_responses.pop(0)

    def close(self) -> None:
        return None


def test_resolve_bootstrap_model() -> None:
    assert resolve_bootstrap_model("auto", "distil-small") == "distil-small"
    assert resolve_bootstrap_model("Systran/faster-whisper-base", "distil-small") == (
        "Systran/faster-whisper-base"
    )


def test_ensure_model_ready_returns_immediately_when_ready() -> None:
    client = _FakeClient([_FakeResponse(200, {"status": "ready"})])

    result = ensure_model_ready(
        base_url="http://127.0.0.1:8000",
        api_key=None,
        model_id="distil-small",
        timeout_seconds=5,
        client=client,
    )

    assert result.model_id == "distil-small"
    assert result.pulled is False
    assert client.post_calls == 0


def test_ensure_model_ready_triggers_pull_when_missing() -> None:
    client = _FakeClient(
        [
            _FakeResponse(200, {"status": "not_downloaded"}),
            _FakeResponse(200, {"status": "ready"}),
        ]
    )

    result = ensure_model_ready(
        base_url="http://127.0.0.1:8000",
        api_key=None,
        model_id="distil-small",
        timeout_seconds=5,
        poll_interval_seconds=0.0,
        client=client,
    )

    assert result.pulled is True
    assert client.post_calls == 1


def test_ensure_model_ready_raises_on_failed_status() -> None:
    client = _FakeClient(
        [
            _FakeResponse(200, {"status": "not_downloaded"}),
            _FakeResponse(200, {"status": "failed", "metadata": {"error": "boom"}}),
        ]
    )

    with pytest.raises(ModelBootstrapError, match="boom"):
        ensure_model_ready(
            base_url="http://127.0.0.1:8000",
            api_key=None,
            model_id="distil-small",
            timeout_seconds=5,
            poll_interval_seconds=0.0,
            client=client,
        )


def test_ensure_model_ready_raises_timeout() -> None:
    client = _FakeClient(
        [
            _FakeResponse(200, {"status": "downloading"}),
        ]
    )

    with pytest.raises(ModelBootstrapError, match="Timed out"):
        ensure_model_ready(
            base_url="http://127.0.0.1:8000",
            api_key=None,
            model_id="distil-small",
            timeout_seconds=0,
            poll_interval_seconds=0.0,
            client=client,
        )


def test_ensure_model_ready_raises_when_model_missing() -> None:
    client = _FakeClient([_FakeResponse(404, {"detail": "not found"})])

    with pytest.raises(ModelBootstrapError, match="not registered"):
        ensure_model_ready(
            base_url="http://127.0.0.1:8000",
            api_key=None,
            model_id="distil-small",
            timeout_seconds=5,
            client=client,
        )


def test_read_model_status_returns_status_and_error() -> None:
    client = _FakeClient(
        [_FakeResponse(200, {"status": "failed", "metadata": {"error": "bad model"}})]
    )

    status = read_model_status(
        base_url="http://127.0.0.1:8000",
        api_key=None,
        model_id="distil-small",
        client=client,
    )

    assert status.model_id == "distil-small"
    assert status.status == "failed"
    assert status.error == "bad model"
