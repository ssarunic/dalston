"""Tests for CLI commands."""

from click.testing import CliRunner

from dalston_cli.main import cli


def test_cli_help():
    """Test CLI help output."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Dalston CLI" in result.output


def test_cli_version():
    """Test CLI version output."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_transcribe_help():
    """Test transcribe command help."""
    runner = CliRunner()
    result = runner.invoke(cli, ["transcribe", "--help"])
    assert result.exit_code == 0
    assert "Transcribe audio files" in result.output


def test_transcribe_no_args():
    """Test transcribe command without arguments."""
    runner = CliRunner()
    result = runner.invoke(cli, ["transcribe"])
    assert result.exit_code == 2  # Missing required argument


def test_listen_help():
    """Test listen command help."""
    runner = CliRunner()
    result = runner.invoke(cli, ["listen", "--help"])
    assert result.exit_code == 0
    assert "Real-time transcription" in result.output


def test_jobs_help():
    """Test jobs command help."""
    runner = CliRunner()
    result = runner.invoke(cli, ["jobs", "--help"])
    assert result.exit_code == 0
    assert "Manage transcription jobs" in result.output


def test_jobs_list_help():
    """Test jobs list subcommand help."""
    runner = CliRunner()
    result = runner.invoke(cli, ["jobs", "list", "--help"])
    assert result.exit_code == 0
    assert "List transcription jobs" in result.output


def test_export_help():
    """Test export command help."""
    runner = CliRunner()
    result = runner.invoke(cli, ["export", "--help"])
    assert result.exit_code == 0
    assert "Export transcript" in result.output


def test_status_help():
    """Test status command help."""
    runner = CliRunner()
    result = runner.invoke(cli, ["status", "--help"])
    assert result.exit_code == 0
    assert "Show server and system status" in result.output
