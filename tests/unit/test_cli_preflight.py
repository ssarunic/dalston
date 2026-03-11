from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from dalston_cli.bootstrap.preflight import PreflightError, run_preflight
from dalston_cli.bootstrap.settings import load_bootstrap_settings


def _make_audio_file(path: Path) -> Path:
    path.write_bytes(b"RIFF....WAVEfmt ")
    return path


def test_load_bootstrap_settings_defaults(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("DALSTON_BOOTSTRAP", raising=False)
    monkeypatch.delenv("DALSTON_DEFAULT_MODEL", raising=False)
    monkeypatch.delenv("DALSTON_LOCAL_SERVER_URL", raising=False)
    settings = load_bootstrap_settings()

    assert settings.enabled is True
    assert settings.default_model == "distil-small"
    assert settings.local_server_url == "http://127.0.0.1:8000"
    assert settings.run_dir == tmp_path / ".dalston" / "run"
    assert settings.lock_file == tmp_path / ".dalston" / "run" / "bootstrap.lock"


def test_run_preflight_creates_engine_id_dirs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = load_bootstrap_settings()
    settings = settings.__class__(**{**settings.__dict__, "min_free_bytes": 0})
    audio = _make_audio_file(tmp_path / "audio.wav")

    report = run_preflight(files=[audio], settings=settings)

    assert report.checked_files == (audio,)
    assert settings.run_dir.exists()
    assert settings.log_dir.exists()


def test_run_preflight_rejects_non_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = load_bootstrap_settings()
    settings = settings.__class__(**{**settings.__dict__, "min_free_bytes": 0})
    non_file = tmp_path / "dir"
    non_file.mkdir()

    with pytest.raises(PreflightError, match="not a file"):
        run_preflight(files=[non_file], settings=settings)


def test_run_preflight_fails_when_uvicorn_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = load_bootstrap_settings()
    settings = settings.__class__(**{**settings.__dict__, "min_free_bytes": 0})
    audio = _make_audio_file(tmp_path / "audio.wav")

    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(PreflightError, match="uvicorn"):
        run_preflight(files=[audio], settings=settings)


def test_run_preflight_skips_uvicorn_check_when_bootstrap_disabled(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("DALSTON_BOOTSTRAP", "false")
    settings = load_bootstrap_settings()
    settings = settings.__class__(**{**settings.__dict__, "min_free_bytes": 0})
    audio = _make_audio_file(tmp_path / "audio.wav")

    monkeypatch.setattr(shutil, "which", lambda _: None)
    report = run_preflight(files=[audio], settings=settings)
    assert report.checked_files == (audio,)
