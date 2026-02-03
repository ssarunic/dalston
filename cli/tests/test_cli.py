"""Tests for CLI commands."""

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
