from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import dalston_cli.main as cli_main
from dalston_cli.bootstrap.server_manager import ServerReadyResult
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

    def health(self):
        return type("Health", (), {"status": "healthy"})()

    def transcribe(self, **kwargs):
        self.transcribe_models.append(kwargs["model"])
        return _FakeJob(
            id="job_123",
            status=_PendingStatus(),
            created_at=datetime.now(UTC),
        )


def test_local_zero_config_bootstrap_uses_default_model(
    monkeypatch,
    tmp_path: Path,
) -> None:
    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"RIFF....WAVEfmt ")

    _FakeDalston.instances.clear()
    monkeypatch.setattr(cli_main, "Dalston", _FakeDalston)

    mock_preflight = Mock(return_value=None)
    mock_model_ready = Mock(return_value=None)
    monkeypatch.setattr(cli_main.transcribe, "run_preflight", mock_preflight)
    monkeypatch.setattr(
        cli_main.transcribe,
        "ensure_local_server_ready",
        Mock(return_value=ServerReadyResult(started=True, skipped=False, managed=True)),
    )
    monkeypatch.setattr(cli_main.transcribe, "ensure_model_ready", mock_model_ready)

    result = runner.invoke(
        cli_main.app,
        [
            "--server",
            "http://127.0.0.1:8000",
            "--quiet",
            "transcribe",
            str(audio_file),
            "--no-wait",
        ],
    )

    assert result.exit_code == 0
    assert len(_FakeDalston.instances) == 1
    assert _FakeDalston.instances[0].transcribe_models == ["distil-small"]
    mock_preflight.assert_called_once()
    assert mock_preflight.call_args.kwargs["files"] == [audio_file]
    mock_model_ready.assert_called_once()
    assert mock_model_ready.call_args.kwargs["model_id"] == "distil-small"


def test_local_existing_server_keeps_auto_model(
    monkeypatch,
    tmp_path: Path,
) -> None:
    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"RIFF....WAVEfmt ")

    _FakeDalston.instances.clear()
    monkeypatch.setattr(cli_main, "Dalston", _FakeDalston)

    mock_preflight = Mock(return_value=None)
    mock_model_ready = Mock(return_value=None)
    monkeypatch.setattr(cli_main.transcribe, "run_preflight", mock_preflight)
    monkeypatch.setattr(
        cli_main.transcribe,
        "ensure_local_server_ready",
        Mock(
            return_value=ServerReadyResult(started=False, skipped=False, managed=False)
        ),
    )
    monkeypatch.setattr(cli_main.transcribe, "ensure_model_ready", mock_model_ready)

    result = runner.invoke(
        cli_main.app,
        [
            "--server",
            "http://127.0.0.1:8000",
            "--quiet",
            "transcribe",
            str(audio_file),
            "--no-wait",
        ],
    )

    assert result.exit_code == 0
    assert len(_FakeDalston.instances) == 1
    assert _FakeDalston.instances[0].transcribe_models == ["auto"]
    mock_preflight.assert_called_once()
    mock_model_ready.assert_not_called()


def test_local_managed_lite_server_keeps_auto_model(
    monkeypatch,
    tmp_path: Path,
) -> None:
    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"RIFF....WAVEfmt ")

    _FakeDalston.instances.clear()
    monkeypatch.setattr(cli_main, "Dalston", _FakeDalston)
    monkeypatch.setenv("DALSTON_MODE", "lite")

    mock_preflight = Mock(return_value=None)
    mock_model_ready = Mock(return_value=None)
    monkeypatch.setattr(cli_main.transcribe, "run_preflight", mock_preflight)
    monkeypatch.setattr(
        cli_main.transcribe,
        "ensure_local_server_ready",
        Mock(return_value=ServerReadyResult(started=True, skipped=False, managed=True)),
    )
    monkeypatch.setattr(cli_main.transcribe, "ensure_model_ready", mock_model_ready)

    result = runner.invoke(
        cli_main.app,
        [
            "--server",
            "http://127.0.0.1:8000",
            "--quiet",
            "transcribe",
            str(audio_file),
            "--no-wait",
        ],
    )

    assert result.exit_code == 0
    assert len(_FakeDalston.instances) == 1
    assert _FakeDalston.instances[0].transcribe_models == ["auto"]
    mock_preflight.assert_called_once()
    mock_model_ready.assert_not_called()


def test_bootstrap_disabled_keeps_auto_model_without_model_registry_check(
    monkeypatch,
    tmp_path: Path,
) -> None:
    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"RIFF....WAVEfmt ")

    _FakeDalston.instances.clear()
    monkeypatch.setattr(cli_main, "Dalston", _FakeDalston)
    monkeypatch.setenv("DALSTON_BOOTSTRAP", "false")

    mock_preflight = Mock(return_value=None)
    mock_server_ready = Mock(return_value=None)
    mock_read_model_status = Mock(return_value=None)
    monkeypatch.setattr(cli_main.transcribe, "run_preflight", mock_preflight)
    monkeypatch.setattr(
        cli_main.transcribe,
        "ensure_local_server_ready",
        mock_server_ready,
    )
    monkeypatch.setattr(
        cli_main.transcribe,
        "read_model_status",
        mock_read_model_status,
    )

    result = runner.invoke(
        cli_main.app,
        [
            "--server",
            "http://127.0.0.1:8000",
            "--quiet",
            "transcribe",
            str(audio_file),
            "--no-wait",
        ],
    )

    assert result.exit_code == 0
    assert len(_FakeDalston.instances) == 1
    assert _FakeDalston.instances[0].transcribe_models == ["auto"]
    mock_preflight.assert_called_once()
    mock_server_ready.assert_not_called()
    mock_read_model_status.assert_not_called()


def test_bootstrap_disabled_validates_explicit_model_readiness(
    monkeypatch,
    tmp_path: Path,
) -> None:
    audio_file = tmp_path / "sample.wav"
    audio_file.write_bytes(b"RIFF....WAVEfmt ")

    _FakeDalston.instances.clear()
    monkeypatch.setattr(cli_main, "Dalston", _FakeDalston)
    monkeypatch.setenv("DALSTON_BOOTSTRAP", "false")

    mock_preflight = Mock(return_value=None)
    mock_server_ready = Mock(return_value=None)
    mock_read_model_status = Mock(
        return_value=type("ModelStatus", (), {"status": "ready", "error": None})()
    )
    monkeypatch.setattr(cli_main.transcribe, "run_preflight", mock_preflight)
    monkeypatch.setattr(
        cli_main.transcribe,
        "ensure_local_server_ready",
        mock_server_ready,
    )
    monkeypatch.setattr(
        cli_main.transcribe,
        "read_model_status",
        mock_read_model_status,
    )

    result = runner.invoke(
        cli_main.app,
        [
            "--server",
            "http://127.0.0.1:8000",
            "--quiet",
            "transcribe",
            str(audio_file),
            "--model",
            "distil-small",
            "--no-wait",
        ],
    )

    assert result.exit_code == 0
    assert len(_FakeDalston.instances) == 1
    assert _FakeDalston.instances[0].transcribe_models == ["distil-small"]
    mock_preflight.assert_called_once()
    mock_server_ready.assert_not_called()
    mock_read_model_status.assert_called_once()
