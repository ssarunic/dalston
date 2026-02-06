"""Shared fixtures and helpers for CLI-based end-to-end tests."""

import json
import subprocess
from pathlib import Path

import pytest

AUDIO_DIR = Path(__file__).parent.parent / "audio"


@pytest.fixture
def audio_dir():
    """Path to the test audio files directory."""
    return AUDIO_DIR


def run_dalston(*args, timeout=180):
    """Run the ``dalston`` CLI and return the CompletedProcess."""
    return subprocess.run(
        ["dalston", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def transcribe_json(audio_file, *extra_args, timeout=180):
    """Run ``dalston transcribe --format json --wait`` and return parsed output."""
    result = run_dalston(
        "transcribe",
        str(audio_file),
        "--format",
        "json",
        "--wait",
        *extra_args,
        timeout=timeout,
    )
    assert result.returncode == 0, (
        f"CLI exited with code {result.returncode}\nstderr: {result.stderr}"
    )
    return json.loads(result.stdout, strict=False)
