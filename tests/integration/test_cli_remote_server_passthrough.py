from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import dalston_cli.main as cli_main
from typer.testing import CliRunner

runner = CliRunner()


class _PendingStatus:
    value = "pending"


@dataclass
class _FakeJob:
    id: str
    status: _PendingStatus
    created_at: datetime
    display_name: str | None = None


class _FakeDalston:
    instances: list[_FakeDalston] = []

    def __init__(
        self, base_url: str, api_key: str | None = None, timeout: float = 120.0
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.transcribe_models: list[str] = []
        _FakeDalston.instances.append(self)

    def transcribe(self, **kwargs):
        self.transcribe_models.append(kwargs["model"])
        return _FakeJob(
            id="job_456",
            status=_PendingStatus(),
            created_at=datetime.now(UTC),
        )


def test_remote_server_bypasses_local_bootstrap(monkeypatch, tmp_path: Path) -> None:
    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"RIFF....WAVEfmt ")

    _FakeDalston.instances.clear()
    monkeypatch.setattr(cli_main, "Dalston", _FakeDalston)

    mock_preflight = Mock(return_value=None)
    mock_server_ready = Mock(return_value=None)
    mock_model_ready = Mock(return_value=None)
    monkeypatch.setattr(cli_main.transcribe, "run_preflight", mock_preflight)
    monkeypatch.setattr(
        cli_main.transcribe,
        "ensure_local_server_ready",
        mock_server_ready,
    )
    monkeypatch.setattr(cli_main.transcribe, "ensure_model_ready", mock_model_ready)

    result = runner.invoke(
        cli_main.app,
        [
            "--server",
            "https://api.example.com",
            "--quiet",
            "transcribe",
            str(audio_file),
            "--no-wait",
        ],
    )

    assert result.exit_code == 0
    assert len(_FakeDalston.instances) == 1
    # Remote keeps CLI default model argument unchanged.
    assert _FakeDalston.instances[0].transcribe_models == ["auto"]
    mock_preflight.assert_not_called()
    mock_server_ready.assert_not_called()
    mock_model_ready.assert_not_called()
