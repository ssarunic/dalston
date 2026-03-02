"""Unit tests for model CLI commands (M40.3)."""

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from dalston.gateway.cli import app

runner = CliRunner()


class TestModelList:
    """Tests for 'model ls' command."""

    @patch("dalston.gateway.cli._list_models")
    def test_list_models_empty(self, mock_list: MagicMock):
        """Test listing when no models exist."""
        mock_list.return_value = []

        result = runner.invoke(app, ["model", "ls"])

        assert result.exit_code == 0
        assert "No models found" in result.output

    @patch("dalston.gateway.cli._list_models")
    def test_list_models_with_data(self, mock_list: MagicMock):
        """Test listing models with data."""
        mock_list.return_value = [
            {
                "id": "nvidia/parakeet-tdt-1.1b",
                "name": "Parakeet TDT",
                "runtime": "nemo",
                "stage": "transcribe",
                "status": "ready",
                "size_bytes": 4_500_000_000,
                "downloaded_at": datetime.now(UTC),
            },
            {
                "id": "Systran/faster-whisper-large-v3",
                "name": "Whisper Large V3",
                "runtime": "faster-whisper",
                "stage": "transcribe",
                "status": "not_downloaded",
                "size_bytes": None,
                "downloaded_at": None,
            },
        ]

        result = runner.invoke(app, ["model", "ls"])

        assert result.exit_code == 0
        assert "Found 2 model(s)" in result.output
        assert "nvidia/parakeet-tdt-1.1b" in result.output
        assert "Systran/faster-whisper-large-v3" in result.output
        assert "nemo" in result.output
        assert "ready" in result.output
        assert "not_downloaded" in result.output

    @patch("dalston.gateway.cli._list_models")
    def test_list_models_with_filters(self, mock_list: MagicMock):
        """Test listing with filters."""
        mock_list.return_value = []

        result = runner.invoke(
            app, ["model", "ls", "--stage", "transcribe", "--runtime", "nemo"]
        )

        assert result.exit_code == 0
        mock_list.assert_called_once()

    @patch("dalston.gateway.cli._list_models")
    def test_list_models_downloaded_only(self, mock_list: MagicMock):
        """Test --downloaded filter."""
        mock_list.return_value = []

        result = runner.invoke(app, ["model", "ls", "--downloaded"])

        assert result.exit_code == 0

    @patch("dalston.gateway.cli._list_models")
    def test_list_models_error(self, mock_list: MagicMock):
        """Test error handling."""
        mock_list.side_effect = Exception("Database connection failed")

        result = runner.invoke(app, ["model", "ls"])

        assert result.exit_code == 1
        assert "Error:" in result.output


class TestModelPull:
    """Tests for 'model pull' command."""

    @patch("dalston.gateway.cli._pull_model")
    def test_pull_model_success(self, mock_pull: MagicMock):
        """Test successful model pull."""
        mock_pull.return_value = {
            "id": "nvidia/parakeet-tdt-1.1b",
            "status": "ready",
            "size_bytes": 4_500_000_000,
            "download_path": "/models/hub/models--nvidia--parakeet-tdt-1.1b",
        }

        result = runner.invoke(app, ["model", "pull", "nvidia/parakeet-tdt-1.1b"])

        assert result.exit_code == 0
        assert "downloaded successfully" in result.output
        assert "nvidia/parakeet-tdt-1.1b" in result.output

    @patch("dalston.gateway.cli._pull_model")
    def test_pull_model_not_found(self, mock_pull: MagicMock):
        """Test pulling non-existent model."""
        mock_pull.side_effect = ValueError("Model not found: unknown-model")

        result = runner.invoke(app, ["model", "pull", "unknown-model"])

        assert result.exit_code == 1
        assert "Model not found" in result.output

    @patch("dalston.gateway.cli._pull_model")
    def test_pull_model_with_force(self, mock_pull: MagicMock):
        """Test pulling with --force flag."""
        mock_pull.return_value = {
            "id": "nvidia/parakeet-tdt-1.1b",
            "status": "ready",
            "size_bytes": 4_500_000_000,
            "download_path": "/models/hub/models--nvidia--parakeet-tdt-1.1b",
        }

        result = runner.invoke(
            app, ["model", "pull", "nvidia/parakeet-tdt-1.1b", "--force"]
        )

        assert result.exit_code == 0

    @patch("dalston.gateway.cli._pull_model")
    def test_pull_model_failed_status(self, mock_pull: MagicMock):
        """Test when download fails."""
        mock_pull.return_value = {
            "id": "nvidia/parakeet-tdt-1.1b",
            "status": "failed",
            "size_bytes": None,
            "download_path": None,
        }

        result = runner.invoke(app, ["model", "pull", "nvidia/parakeet-tdt-1.1b"])

        assert result.exit_code == 1
        assert "Download status: failed" in result.output


class TestModelStatus:
    """Tests for 'model status' command."""

    @patch("dalston.gateway.cli._model_status")
    def test_status_downloaded_model(self, mock_status: MagicMock):
        """Test status for downloaded model."""
        now = datetime.now(UTC)
        mock_status.return_value = {
            "id": "nvidia/parakeet-tdt-1.1b",
            "name": "Parakeet TDT 1.1B",
            "runtime": "nemo",
            "runtime_model_id": "nvidia/parakeet-tdt-1.1b",
            "stage": "transcribe",
            "status": "ready",
            "download_path": "/models/hub/models--nvidia--parakeet-tdt-1.1b",
            "size_bytes": 4_500_000_000,
            "downloaded_at": now,
            "languages": ["en"],
            "word_timestamps": True,
            "punctuation": True,
            "streaming": False,
            "supports_cpu": False,
            "min_vram_gb": 4.0,
            "min_ram_gb": 8.0,
            "last_used_at": now,
            "created_at": now,
        }

        result = runner.invoke(app, ["model", "status", "nvidia/parakeet-tdt-1.1b"])

        assert result.exit_code == 0
        assert "nvidia/parakeet-tdt-1.1b" in result.output
        assert "nemo" in result.output
        assert "ready" in result.output
        assert "Word Timestamps: True" in result.output
        assert "CPU Support:     False" in result.output
        assert "Min VRAM:        4.0 GB" in result.output

    @patch("dalston.gateway.cli._model_status")
    def test_status_not_downloaded(self, mock_status: MagicMock):
        """Test status for model that is not downloaded."""
        mock_status.return_value = {
            "id": "Systran/faster-whisper-large-v3",
            "name": None,
            "runtime": "faster-whisper",
            "runtime_model_id": "Systran/faster-whisper-large-v3",
            "stage": "transcribe",
            "status": "not_downloaded",
            "download_path": None,
            "size_bytes": None,
            "downloaded_at": None,
            "languages": None,
            "word_timestamps": False,
            "punctuation": False,
            "streaming": False,
            "supports_cpu": True,
            "min_vram_gb": None,
            "min_ram_gb": None,
            "last_used_at": None,
            "created_at": datetime.now(UTC),
        }

        result = runner.invoke(
            app, ["model", "status", "Systran/faster-whisper-large-v3"]
        )

        assert result.exit_code == 0
        assert "not_downloaded" in result.output
        assert "multilingual" in result.output

    @patch("dalston.gateway.cli._model_status")
    def test_status_model_not_found(self, mock_status: MagicMock):
        """Test status for non-existent model."""
        mock_status.return_value = None

        result = runner.invoke(app, ["model", "status", "unknown-model"])

        assert result.exit_code == 1
        assert "Model not found: unknown-model" in result.output


class TestModelRemove:
    """Tests for 'model rm' command."""

    @patch("dalston.gateway.cli._remove_model")
    def test_remove_model_with_confirm(self, mock_remove: MagicMock):
        """Test removing model with confirmation."""
        mock_remove.return_value = None

        result = runner.invoke(
            app, ["model", "rm", "nvidia/parakeet-tdt-1.1b"], input="y\n"
        )

        assert result.exit_code == 0
        assert "Model nvidia/parakeet-tdt-1.1b removed" in result.output

    @patch("dalston.gateway.cli._remove_model")
    def test_remove_model_with_yes_flag(self, mock_remove: MagicMock):
        """Test removing model with --yes flag (skip confirmation)."""
        mock_remove.return_value = None

        result = runner.invoke(
            app, ["model", "rm", "nvidia/parakeet-tdt-1.1b", "--yes"]
        )

        assert result.exit_code == 0
        assert "Model nvidia/parakeet-tdt-1.1b removed" in result.output

    @patch("dalston.gateway.cli._remove_model")
    def test_remove_model_declined(self, mock_remove: MagicMock):
        """Test removing model when user declines confirmation."""
        result = runner.invoke(
            app, ["model", "rm", "nvidia/parakeet-tdt-1.1b"], input="n\n"
        )

        assert result.exit_code == 1  # Aborted
        mock_remove.assert_not_called()

    @patch("dalston.gateway.cli._remove_model")
    def test_remove_model_not_found(self, mock_remove: MagicMock):
        """Test removing non-existent model."""
        mock_remove.side_effect = ValueError("Model not found: unknown-model")

        result = runner.invoke(app, ["model", "rm", "unknown-model", "--yes"])

        assert result.exit_code == 1
        assert "Model not found" in result.output


class TestModelSync:
    """Tests for 'model sync' command."""

    @patch("dalston.gateway.cli._sync_models")
    def test_sync_models(self, mock_sync: MagicMock):
        """Test syncing models."""
        mock_sync.return_value = {"updated": 3, "unchanged": 10}

        result = runner.invoke(app, ["model", "sync"])

        assert result.exit_code == 0
        assert "Syncing model registry with disk" in result.output
        assert "3 updated" in result.output
        assert "10 unchanged" in result.output

    @patch("dalston.gateway.cli._sync_models")
    def test_sync_models_no_changes(self, mock_sync: MagicMock):
        """Test syncing when no changes needed."""
        mock_sync.return_value = {"updated": 0, "unchanged": 15}

        result = runner.invoke(app, ["model", "sync"])

        assert result.exit_code == 0
        assert "0 updated" in result.output
        assert "15 unchanged" in result.output

    @patch("dalston.gateway.cli._sync_models")
    def test_sync_models_error(self, mock_sync: MagicMock):
        """Test sync error handling."""
        mock_sync.side_effect = Exception("Database connection failed")

        result = runner.invoke(app, ["model", "sync"])

        assert result.exit_code == 1
        assert "Error:" in result.output
