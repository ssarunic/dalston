"""Tests for CLI commands."""

import pytest
from typer.testing import CliRunner

from dalston_cli.main import app

runner = CliRunner()


def test_cli_help():
    """Test CLI help output."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Dalston CLI" in result.output


def test_cli_version():
    """Test CLI version output."""
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_transcribe_help():
    """Test transcribe command help."""
    result = runner.invoke(app, ["transcribe", "--help"])
    assert result.exit_code == 0
    assert "Transcribe audio files" in result.output


def test_transcribe_no_args():
    """Test transcribe command without arguments."""
    result = runner.invoke(app, ["transcribe"])
    assert result.exit_code == 2  # Missing required argument


def test_listen_help():
    """Test listen command help."""
    result = runner.invoke(app, ["listen", "--help"])
    assert result.exit_code == 0
    assert "Real-time transcription" in result.output


def test_jobs_help():
    """Test jobs command help."""
    result = runner.invoke(app, ["jobs", "--help"])
    assert result.exit_code == 0
    assert "Manage transcription jobs" in result.output


def test_jobs_list_help():
    """Test jobs list subcommand help."""
    result = runner.invoke(app, ["jobs", "list", "--help"])
    assert result.exit_code == 0
    assert "List transcription jobs" in result.output
    assert "--since" in result.output


def test_jobs_list_since_invalid():
    """Test --since rejects garbage values with a helpful message."""
    result = runner.invoke(app, ["jobs", "list", "--since", "garbage"])
    assert result.exit_code == 2
    assert "--since" in result.output
    assert "ISO 8601" in result.output


def test_parse_since_relative_and_absolute():
    """_parse_since handles relative offsets, today/yesterday, and ISO 8601."""
    from datetime import UTC, datetime, timedelta

    from dalston_cli.commands.jobs import _parse_since

    now = datetime.now(UTC)

    assert (now - _parse_since("24h")).total_seconds() == pytest.approx(
        24 * 3600, abs=5
    )
    assert (now - _parse_since("90m")).total_seconds() == pytest.approx(90 * 60, abs=5)
    assert (now - _parse_since("7d")).total_seconds() == pytest.approx(7 * 86400, abs=5)

    today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    assert _parse_since("today") == today_midnight
    assert _parse_since("yesterday") == today_midnight - timedelta(days=1)

    assert _parse_since("2026-05-13T17:23:00Z") == datetime(
        2026, 5, 13, 17, 23, tzinfo=UTC
    )
    # Naive timestamps default to UTC
    assert _parse_since("2026-05-13").tzinfo is not None


def test_export_help():
    """Test export command help."""
    result = runner.invoke(app, ["export", "--help"])
    assert result.exit_code == 0
    assert "Export transcript" in result.output


def test_status_help():
    """Test status command help."""
    result = runner.invoke(app, ["status", "--help"])
    assert result.exit_code == 0
    assert "Show server and system status" in result.output
