"""Tests for model pull command progress UX."""

from __future__ import annotations

from dalston_cli.commands import models as models_cmd
from dalston_cli.main import state


def test_format_bytes_human_readable() -> None:
    assert models_cmd._format_bytes(None) == "-"
    assert models_cmd._format_bytes(0) == "0 B"
    assert models_cmd._format_bytes(1024) == "1.0 KB"


def test_pull_model_no_watch_starts_server_pull(monkeypatch) -> None:
    calls: list[str] = []

    def fake_fetch(_base_url: str, _api_key: str | None, model_id: str) -> dict:
        calls.append(f"fetch:{model_id}")
        return {
            "id": model_id,
            "engine_id": "nemo",
            "source": "nvidia/parakeet-ctc-0.6b",
            "status": "not_downloaded",
        }

    def fake_trigger(
        _base_url: str, _api_key: str | None, model_id: str, force: bool
    ) -> dict:
        calls.append(f"pull:{model_id}:{force}")
        return {"status": "downloading", "message": "Download started"}

    monkeypatch.setattr(models_cmd, "_fetch_registry_entry", fake_fetch)
    monkeypatch.setattr(models_cmd, "_trigger_pull", fake_trigger)

    state.server = "http://localhost:8000"
    state.api_key = "test-key"

    models_cmd.pull_model("nvidia/parakeet-ctc-0.6b", force=False, watch=False)

    assert calls == [
        "fetch:nvidia/parakeet-ctc-0.6b",
        "pull:nvidia/parakeet-ctc-0.6b:False",
    ]


def test_pull_model_watch_until_ready(monkeypatch) -> None:
    responses = [
        {
            "id": "nvidia/parakeet-ctc-0.6b",
            "engine_id": "nemo",
            "source": "nvidia/parakeet-ctc-0.6b",
            "status": "not_downloaded",
        },
        {
            "status": "downloading",
            "download_progress": 10,
            "downloaded_bytes": 100,
            "expected_total_bytes": 1000,
            "metadata": {},
        },
        {
            "status": "ready",
            "download_progress": 100,
            "downloaded_bytes": 1000,
            "expected_total_bytes": 1000,
            "size_bytes": 1000,
            "metadata": {},
        },
    ]

    def fake_fetch(_base_url: str, _api_key: str | None, _model_id: str) -> dict:
        return responses.pop(0)

    monkeypatch.setattr(models_cmd, "_fetch_registry_entry", fake_fetch)
    monkeypatch.setattr(
        models_cmd,
        "_trigger_pull",
        lambda *_args, **_kwargs: {
            "status": "downloading",
            "message": "Download started",
        },
    )
    monkeypatch.setattr(models_cmd.time, "sleep", lambda *_args, **_kwargs: None)

    state.server = "http://localhost:8000"
    state.api_key = None

    models_cmd.pull_model("nvidia/parakeet-ctc-0.6b", force=False, watch=True)

    assert responses == []
