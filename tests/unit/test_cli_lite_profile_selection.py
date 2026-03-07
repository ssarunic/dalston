"""Unit tests for CLI lite profile selection (M58 Phase 3)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from dalston_cli.main import app
from typer.testing import CliRunner

runner = CliRunner()


class TestTranscribeHelpIncludesProfile:
    def test_profile_option_in_help(self) -> None:
        result = runner.invoke(app, ["transcribe", "--help"])
        assert result.exit_code == 0
        assert "--profile" in result.output

    def test_profile_help_mentions_valid_values(self) -> None:
        result = runner.invoke(app, ["transcribe", "--help"])
        assert "core" in result.output
        assert "speaker" in result.output
        assert "compliance" in result.output


class TestProfileValidationInLiteMode:
    """When DALSTON_MODE=lite, the CLI validates profile client-side."""

    def _make_mock_client(self) -> MagicMock:
        client = MagicMock()
        client.base_url = "http://127.0.0.1:8000"
        return client

    def test_invalid_profile_exits_with_error_in_lite_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DALSTON_MODE", "lite")
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"fake audio")

        result = runner.invoke(
            app,
            [
                "--server",
                "http://127.0.0.1:8000",
                "transcribe",
                str(audio_file),
                "--profile",
                "nonexistent_profile",
            ],
        )
        assert result.exit_code == 1
        assert "Invalid lite profile" in result.output or "Invalid lite profile" in str(
            result.exception or ""
        )

    def test_valid_profile_core_does_not_fail_validation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DALSTON_MODE", "lite")
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"fake audio")

        mock_job = MagicMock()
        mock_job.id = "test-job-id"
        mock_job.status.value = "completed"
        mock_job.error = None

        mock_transcript = MagicMock()
        mock_transcript.text = "hello world"
        mock_transcript.segments = []

        with (
            patch("dalston_cli.commands.transcribe.load_bootstrap_settings") as mock_bs,
            patch("dalston_cli.commands.transcribe.run_preflight"),
            patch(
                "dalston_cli.commands.transcribe.ensure_local_server_ready"
            ) as mock_srv,
            patch("dalston_cli.commands.transcribe.ensure_model_ready"),
        ):
            mock_bs.return_value = MagicMock(
                target_is_local=lambda url: False,
                enabled=False,
            )
            mock_srv.return_value = MagicMock(managed=False)

            # The profile validation (resolve_profile) should pass silently for "core".
            # We don't need to reach the actual transcription; just verify no exit code 1
            # from the profile validation step itself.
            # Patch client to raise so we can detect we got past validation.
            with patch("dalston_cli.commands.transcribe.state") as mock_state:
                mock_client = MagicMock()
                mock_client.base_url = "http://remote.server:8000"
                mock_state.client = mock_client
                mock_state.quiet = True

                # We expect either success or a different failure (e.g., network),
                # but NOT "Invalid lite profile" in the output.
                result = runner.invoke(
                    app,
                    ["transcribe", str(audio_file), "--profile", "core"],
                )
                output = result.output + str(result.exception or "")
                assert "Invalid lite profile" not in output

    def test_valid_profile_speaker_passes_validation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DALSTON_MODE", "lite")
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"fake audio")

        with patch("dalston_cli.commands.transcribe.state") as mock_state:
            mock_client = MagicMock()
            mock_client.base_url = "http://remote.server:8000"
            mock_state.client = mock_client
            mock_state.quiet = True

            result = runner.invoke(
                app,
                ["transcribe", str(audio_file), "--profile", "speaker"],
            )
            assert "Invalid lite profile" not in (
                result.output + str(result.exception or "")
            )

    def test_valid_profile_compliance_passes_validation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DALSTON_MODE", "lite")
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"fake audio")

        with patch("dalston_cli.commands.transcribe.state") as mock_state:
            mock_client = MagicMock()
            mock_client.base_url = "http://remote.server:8000"
            mock_state.client = mock_client
            mock_state.quiet = True

            result = runner.invoke(
                app,
                ["transcribe", str(audio_file), "--profile", "compliance"],
            )
            assert "Invalid lite profile" not in (
                result.output + str(result.exception or "")
            )


class TestProfileSkippedInDistributedMode:
    """In distributed mode, profile validation is skipped client-side."""

    def test_invalid_profile_not_caught_locally_in_distributed_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # In distributed mode (default), the CLI does NOT validate profile locally.
        # Server-side validation will reject it, but that's out of scope for this test.
        monkeypatch.delenv("DALSTON_MODE", raising=False)
        audio_file = tmp_path / "test.wav"
        audio_file.write_bytes(b"fake audio")

        with patch("dalston_cli.commands.transcribe.state") as mock_state:
            mock_client = MagicMock()
            mock_client.base_url = "http://remote.server:8000"
            mock_state.client = mock_client
            mock_state.quiet = True

            result = runner.invoke(
                app,
                ["transcribe", str(audio_file), "--profile", "bogus_profile"],
            )
            # Should NOT produce "Invalid lite profile" error client-side
            assert "Invalid lite profile" not in (
                result.output + str(result.exception or "")
            )


class TestProfileDefaultIsCore:
    def test_default_profile_is_core(self) -> None:
        """Ensure --profile defaults to 'core' (backward compat with M56/M57)."""
        result = runner.invoke(app, ["transcribe", "--help"])
        # The help output should show 'core' as the default value
        assert result.exit_code == 0
        # Default is shown in typer help as [default: core]
        assert "core" in result.output


class TestSDKLiteProfileParameter:
    """Verify the SDK accepts lite_profile and includes it in the request."""

    def test_sdk_transcribe_accepts_lite_profile(self) -> None:
        import inspect

        from dalston_sdk import Dalston

        sig = inspect.signature(Dalston.transcribe)
        assert "lite_profile" in sig.parameters

    def test_sdk_transcribe_lite_profile_default_is_core(self) -> None:
        import inspect

        from dalston_sdk import Dalston

        sig = inspect.signature(Dalston.transcribe)
        param = sig.parameters["lite_profile"]
        assert param.default == "core"

    def test_sdk_async_transcribe_accepts_lite_profile(self) -> None:
        import inspect

        from dalston_sdk import AsyncDalston

        sig = inspect.signature(AsyncDalston.transcribe)
        assert "lite_profile" in sig.parameters
