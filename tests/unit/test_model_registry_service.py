"""Unit tests for ModelRegistryService."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dalston.db.models import ModelRegistryModel
from dalston.gateway.services.model_registry import (
    ModelNotDownloadedError,
    ModelNotFoundError,
    ModelRegistryService,
)


@pytest.fixture
def service() -> ModelRegistryService:
    """Create a ModelRegistryService instance."""
    return ModelRegistryService()


@pytest.fixture
def mock_db() -> AsyncMock:
    """Create a mock async database session."""
    return AsyncMock()


@pytest.fixture
def sample_model() -> ModelRegistryModel:
    """Create a sample model for testing."""
    return ModelRegistryModel(
        id="faster-whisper-large-v3",
        name="Faster Whisper Large V3",
        engine_id="faster-whisper",
        loaded_model_id="Systran/faster-whisper-large-v3",
        stage="transcribe",
        status="ready",
        download_path="/models/huggingface/hub/models--Systran--faster-whisper-large-v3",
        size_bytes=3_000_000_000,
        downloaded_at=datetime.now(UTC),
        source="huggingface",
        library_name="ctranslate2",
        languages=["en", "es", "fr"],
        word_timestamps=True,
        punctuation=True,
        streaming=False,
        min_vram_gb=4.0,
        supports_cpu=True,
        model_metadata={},
    )


class TestGetModel:
    """Tests for ModelRegistryService.get_model."""

    @pytest.mark.asyncio
    async def test_get_model_found(
        self,
        service: ModelRegistryService,
        mock_db: AsyncMock,
        sample_model: ModelRegistryModel,
    ):
        """Test getting a model that exists."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_model
        mock_db.execute.return_value = mock_result

        result = await service.get_model(mock_db, "faster-whisper-large-v3")

        assert result == sample_model
        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_model_not_found(
        self,
        service: ModelRegistryService,
        mock_db: AsyncMock,
    ):
        """Test getting a model that doesn't exist."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        result = await service.get_model(mock_db, "nonexistent-model")

        assert result is None


class TestGetModelOrRaise:
    """Tests for ModelRegistryService.get_model_or_raise."""

    @pytest.mark.asyncio
    async def test_get_model_or_raise_found(
        self,
        service: ModelRegistryService,
        mock_db: AsyncMock,
        sample_model: ModelRegistryModel,
    ):
        """Test getting a model that exists."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_model
        mock_db.execute.return_value = mock_result

        result = await service.get_model_or_raise(mock_db, "faster-whisper-large-v3")

        assert result == sample_model

    @pytest.mark.asyncio
    async def test_get_model_or_raise_not_found(
        self,
        service: ModelRegistryService,
        mock_db: AsyncMock,
    ):
        """Test getting a model that doesn't exist raises error."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with pytest.raises(ModelNotFoundError) as exc_info:
            await service.get_model_or_raise(mock_db, "nonexistent-model")

        assert exc_info.value.model_id == "nonexistent-model"
        assert "nonexistent-model" in str(exc_info.value)


class TestListModels:
    """Tests for ModelRegistryService.list_models."""

    @pytest.mark.asyncio
    async def test_list_models_no_filters(
        self,
        service: ModelRegistryService,
        mock_db: AsyncMock,
        sample_model: ModelRegistryModel,
    ):
        """Test listing all models without filters."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [sample_model]
        mock_db.execute.return_value = mock_result

        result = await service.list_models(mock_db)

        assert len(result) == 1
        assert result[0] == sample_model

    @pytest.mark.asyncio
    async def test_list_models_with_stage_filter(
        self,
        service: ModelRegistryService,
        mock_db: AsyncMock,
    ):
        """Test listing models filtered by stage."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = mock_result

        await service.list_models(mock_db, stage="diarize")

        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_models_with_engine_id_filter(
        self,
        service: ModelRegistryService,
        mock_db: AsyncMock,
    ):
        """Test listing models filtered by engine_id."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = mock_result

        await service.list_models(mock_db, engine_id="nemo")

        mock_db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_list_models_with_status_filter(
        self,
        service: ModelRegistryService,
        mock_db: AsyncMock,
    ):
        """Test listing models filtered by status."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute.return_value = mock_result

        await service.list_models(mock_db, status="ready")

        mock_db.execute.assert_called_once()


class TestEnsureReady:
    """Tests for ModelRegistryService.ensure_ready."""

    @pytest.mark.asyncio
    async def test_ensure_ready_model_ready(
        self,
        service: ModelRegistryService,
        mock_db: AsyncMock,
        sample_model: ModelRegistryModel,
    ):
        """Test ensure_ready with a downloaded model."""
        sample_model.status = "ready"
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_model
        mock_db.execute.return_value = mock_result

        result = await service.ensure_ready(mock_db, "faster-whisper-large-v3")

        assert result == sample_model
        assert result.status == "ready"

    @pytest.mark.asyncio
    async def test_ensure_ready_model_not_downloaded(
        self,
        service: ModelRegistryService,
        mock_db: AsyncMock,
        sample_model: ModelRegistryModel,
    ):
        """Test ensure_ready with a model that isn't downloaded."""
        sample_model.status = "not_downloaded"
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_model
        mock_db.execute.return_value = mock_result

        with pytest.raises(ModelNotDownloadedError) as exc_info:
            await service.ensure_ready(mock_db, "faster-whisper-large-v3")

        assert exc_info.value.model_id == "faster-whisper-large-v3"
        assert "dalston model pull" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_ensure_ready_model_downloading(
        self,
        service: ModelRegistryService,
        mock_db: AsyncMock,
        sample_model: ModelRegistryModel,
    ):
        """Test ensure_ready with a model currently downloading."""
        sample_model.status = "downloading"
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_model
        mock_db.execute.return_value = mock_result

        with pytest.raises(ModelNotDownloadedError):
            await service.ensure_ready(mock_db, "faster-whisper-large-v3")

    @pytest.mark.asyncio
    async def test_ensure_ready_model_not_found(
        self,
        service: ModelRegistryService,
        mock_db: AsyncMock,
    ):
        """Test ensure_ready with a nonexistent model."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with pytest.raises(ModelNotFoundError):
            await service.ensure_ready(mock_db, "nonexistent-model")


class TestTouchModel:
    """Tests for ModelRegistryService.touch_model."""

    @pytest.mark.asyncio
    async def test_touch_model_updates_timestamp(
        self,
        service: ModelRegistryService,
        mock_db: AsyncMock,
    ):
        """Test that touch_model updates last_used_at."""
        await service.touch_model(mock_db, "faster-whisper-large-v3")

        mock_db.execute.assert_called_once()
        mock_db.commit.assert_called_once()


class TestRegisterModel:
    """Tests for ModelRegistryService.register_model."""

    @pytest.mark.asyncio
    async def test_register_model_minimal(
        self,
        service: ModelRegistryService,
        mock_db: AsyncMock,
    ):
        """Test registering a model with minimal fields."""
        await service.register_model(
            mock_db,
            model_id="test-model",
            engine_id="test-engine_id",
            loaded_model_id="test/model",
            stage="transcribe",
        )

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()
        mock_db.refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_register_model_full(
        self,
        service: ModelRegistryService,
        mock_db: AsyncMock,
    ):
        """Test registering a model with all fields."""
        await service.register_model(
            mock_db,
            model_id="nvidia/parakeet-tdt-1.1b",
            name="Parakeet TDT 1.1B",
            engine_id="nemo",
            loaded_model_id="nvidia/parakeet-tdt-1.1b",
            stage="transcribe",
            source="huggingface",
            library_name="nemo",
            languages=["en"],
            word_timestamps=True,
            punctuation=True,
            streaming=False,
            min_vram_gb=4.0,
            min_ram_gb=8.0,
            supports_cpu=False,
            model_metadata={"author": "nvidia"},
        )

        # register_model calls db.add() once for the model, then once per language
        assert mock_db.add.call_count >= 1

        # Verify the model was created with correct attributes (first add call)
        added_model = mock_db.add.call_args_list[0][0][0]
        assert added_model.id == "nvidia/parakeet-tdt-1.1b"
        assert added_model.name == "Parakeet TDT 1.1B"
        assert added_model.engine_id == "nemo"
        assert added_model.status == "not_downloaded"
        assert added_model.languages == ["en"]
        assert added_model.supports_cpu is False


class TestPullModel:
    """Tests for ModelRegistryService.pull_model."""

    @pytest.mark.asyncio
    async def test_pull_model_already_ready(
        self,
        service: ModelRegistryService,
        mock_db: AsyncMock,
        sample_model: ModelRegistryModel,
    ):
        """Test pulling a model that's already downloaded."""
        sample_model.status = "ready"
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_model
        mock_db.execute.return_value = mock_result

        result = await service.pull_model(mock_db, "faster-whisper-large-v3")

        assert result == sample_model
        # Should not have called commit for downloading status update
        assert mock_db.commit.call_count == 0

    @pytest.mark.asyncio
    async def test_pull_model_not_found(
        self,
        service: ModelRegistryService,
        mock_db: AsyncMock,
    ):
        """Test pulling a nonexistent model raises error."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with pytest.raises(ModelNotFoundError):
            await service.pull_model(mock_db, "nonexistent-model")

    @pytest.mark.asyncio
    async def test_pull_model_force_redownload(
        self,
        service: ModelRegistryService,
        mock_db: AsyncMock,
        sample_model: ModelRegistryModel,
    ):
        """Test force re-downloading a model."""
        sample_model.status = "ready"
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_model
        mock_db.execute.return_value = mock_result

        # Mock async_session used by _progress_poller (creates its own DB session)
        mock_poller_session = AsyncMock()
        mock_poller_ctx = AsyncMock()
        mock_poller_ctx.__aenter__.return_value = mock_poller_session
        mock_poller_ctx.__aexit__.return_value = None

        # Patch snapshot_download, S3 upload, HF resolver, and async_session
        with (
            patch("huggingface_hub.snapshot_download") as mock_download,
            patch.object(service, "_upload_model_to_s3") as mock_s3_upload,
            patch("pathlib.Path.rglob") as mock_rglob,
            patch("shutil.rmtree"),
            patch(
                "dalston.gateway.services.model_registry.HFResolver"
            ) as mock_resolver_cls,
            patch(
                "dalston.db.session.async_session",
                return_value=mock_poller_ctx,
            ),
        ):
            mock_resolver_cls.return_value.get_model_total_size_bytes = AsyncMock(
                return_value=5000
            )
            mock_download.return_value = "/models/test"
            mock_s3_upload.return_value = "s3://bucket/models/test"
            mock_file = MagicMock()
            mock_file.is_file.return_value = True
            mock_file.stat.return_value.st_size = 1000
            mock_rglob.return_value = [mock_file]

            await service.pull_model(mock_db, "faster-whisper-large-v3", force=True)

            mock_download.assert_called_once()
            mock_s3_upload.assert_called_once()


class TestRemoveModel:
    """Tests for ModelRegistryService.remove_model."""

    @pytest.mark.asyncio
    async def test_remove_model_not_found(
        self,
        service: ModelRegistryService,
        mock_db: AsyncMock,
    ):
        """Test removing a nonexistent model raises error."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        with pytest.raises(ModelNotFoundError):
            await service.remove_model(mock_db, "nonexistent-model")

    @pytest.mark.asyncio
    async def test_remove_model_with_files(
        self,
        service: ModelRegistryService,
        mock_db: AsyncMock,
        sample_model: ModelRegistryModel,
    ):
        """Test removing a model deletes files from S3 and updates registry."""
        # Set S3 path for the model
        sample_model.download_path = (
            "s3://dalston-artifacts/models/faster-whisper-large-v3/"
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_model
        mock_db.execute.return_value = mock_result

        with patch.object(service, "_check_model_in_use", return_value=0):
            with patch.object(service, "_delete_model_from_s3") as mock_s3_delete:
                await service.remove_model(mock_db, "faster-whisper-large-v3")

                mock_s3_delete.assert_called_once()
                mock_db.commit.assert_called()


class TestSyncFromS3:
    """Tests for ModelRegistryService.sync_from_s3."""

    @pytest.mark.asyncio
    async def test_sync_from_s3_no_changes(
        self,
        service: ModelRegistryService,
        mock_db: AsyncMock,
        sample_model: ModelRegistryModel,
    ):
        """Test sync when registry matches S3."""
        sample_model.status = "ready"
        sample_model.download_path = "s3://bucket/models/faster-whisper-large-v3/"
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [sample_model]
        mock_db.execute.return_value = mock_result

        with patch.object(service, "_is_model_in_s3", return_value=True):
            result = await service.sync_from_s3(mock_db)

            assert result["unchanged"] == 1
            assert result["updated"] == 0

    @pytest.mark.asyncio
    async def test_sync_from_s3_model_missing(
        self,
        service: ModelRegistryService,
        mock_db: AsyncMock,
        sample_model: ModelRegistryModel,
    ):
        """Test sync when model files are missing from S3."""
        sample_model.status = "ready"
        sample_model.download_path = "s3://bucket/models/faster-whisper-large-v3/"
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [sample_model]
        mock_db.execute.return_value = mock_result

        with patch.object(service, "_is_model_in_s3", return_value=False):
            result = await service.sync_from_s3(mock_db)

            assert result["updated"] == 1
            assert result["unchanged"] == 0


class TestModelNotFoundError:
    """Tests for ModelNotFoundError exception."""

    def test_error_message(self):
        """Test error contains model ID."""
        error = ModelNotFoundError("test-model")

        assert error.model_id == "test-model"
        assert "test-model" in str(error)


class TestModelNotDownloadedError:
    """Tests for ModelNotDownloadedError exception."""

    def test_error_message(self):
        """Test error contains model ID and help text."""
        error = ModelNotDownloadedError("test-model")

        assert error.model_id == "test-model"
        assert "test-model" in str(error)
        assert "dalston model pull" in str(error)


class TestDownloadProgressTracker:
    """Tests for throttled progress tracker behavior."""

    def test_tracker_emits_first_and_throttles_until_threshold(self):
        from dalston.gateway.services.model_registry import DownloadProgressTracker

        tracker = DownloadProgressTracker()

        assert tracker.should_emit() is True
        tracker.mark_emitted()
        tracker.add(1024)
        assert tracker.should_emit() is False

    def test_tracker_emits_after_large_byte_delta(self):
        from dalston.gateway.services.model_registry import (
            DOWNLOAD_PROGRESS_MIN_BYTES,
            DownloadProgressTracker,
        )

        tracker = DownloadProgressTracker()
        tracker.mark_emitted()
        tracker.add(DOWNLOAD_PROGRESS_MIN_BYTES)

        assert tracker.should_emit() is True
