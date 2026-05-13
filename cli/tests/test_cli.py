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


def test_format_speed():
    """_format_speed renders realtime factor reasonably across ranges."""
    from dalston_cli.output import _format_speed

    assert _format_speed(125.5, 50) == "2.5x"
    assert _format_speed(60, 600) == "0.1x"
    assert _format_speed(60, 6000) == "<0.1x"
    assert _format_speed(1000, 10) == "100x"
    assert _format_speed(None, 50) == "-"
    assert _format_speed(50, None) == "-"
    assert _format_speed(50, 0) == "-"


def test_parse_since_relative_and_absolute():
    """_parse_since handles relative offsets, today/yesterday, ISO 8601, and HH:MM."""
    from datetime import UTC, datetime, timedelta

    import typer

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

    # Bare HH:MM resolves to today at that UTC time (or yesterday if future)
    tod = _parse_since("17:23")
    assert tod.tzinfo is not None
    assert tod.minute == 23 and tod.hour == 17
    assert tod <= now

    tod_with_seconds = _parse_since("17:23:45")
    assert tod_with_seconds.second == 45

    # Future HH:MM today rolls to yesterday
    future_hh = (now + timedelta(hours=1)).strftime("%H:%M")
    parsed = _parse_since(future_hh)
    assert (now - parsed).total_seconds() > 23 * 3600

    # Invalid time-of-day rejected
    with pytest.raises(typer.BadParameter):
        _parse_since("25:00")


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
