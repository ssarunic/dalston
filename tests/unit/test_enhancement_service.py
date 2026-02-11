"""Unit tests for EnhancementService (M07 Hybrid Mode)."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest

from dalston.gateway.services.enhancement import (
    EnhancementError,
    EnhancementService,
    create_enhancement_for_session,
)


class TestEnhancementService:
    """Tests for EnhancementService.create_enhancement_job method."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock async database session."""
        db = AsyncMock()
        return db

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings."""
        settings = MagicMock()
        return settings

    @pytest.fixture
    def enhancement_service(self, mock_db, mock_settings):
        """Create EnhancementService instance with mocks."""
        return EnhancementService(mock_db, mock_settings)

    def _make_session(
        self,
        session_id: UUID | None = None,
        tenant_id: UUID | None = None,
        status: str = "completed",
        audio_uri: str | None = "s3://bucket/sessions/test/audio.wav",
        language: str | None = "en",
        model: str | None = "fast",
        engine: str | None = "parakeet",
        enhancement_job_id: UUID | None = None,
    ):
        """Create a mock RealtimeSessionModel."""
        session = MagicMock()
        session.id = session_id or UUID("11111111-1111-1111-1111-111111111111")
        session.tenant_id = tenant_id or UUID("00000000-0000-0000-0000-000000000000")
        session.status = status
        session.audio_uri = audio_uri
        session.language = language
        session.model = model
        session.engine = engine
        session.enhancement_job_id = enhancement_job_id
        return session

    def _make_job(self, job_id: UUID | None = None):
        """Create a mock JobModel."""
        job = MagicMock()
        job.id = job_id or UUID("22222222-2222-2222-2222-222222222222")
        job.status = "pending"
        return job

    @pytest.mark.asyncio
    async def test_create_enhancement_job_success(self, enhancement_service, mock_db):
        """Test creating enhancement job from a valid session."""
        session = self._make_session()
        mock_job = self._make_job()

        with patch.object(
            enhancement_service.jobs_service, "create_job", return_value=mock_job
        ) as mock_create:
            job = await enhancement_service.create_enhancement_job(session)

        assert job is mock_job
        mock_create.assert_awaited_once()

        # Verify job parameters
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["tenant_id"] == session.tenant_id
        assert call_kwargs["audio_uri"] == session.audio_uri
        assert call_kwargs["parameters"]["language"] == "en"
        assert call_kwargs["parameters"]["speaker_detection"] == "diarize"
        assert call_kwargs["parameters"]["timestamps_granularity"] == "word"

    @pytest.mark.asyncio
    async def test_create_enhancement_job_no_audio_raises(
        self, enhancement_service, mock_db
    ):
        """Test that creating enhancement without audio raises EnhancementError."""
        session = self._make_session(audio_uri=None)

        with pytest.raises(EnhancementError, match="session has no recorded audio"):
            await enhancement_service.create_enhancement_job(session)

    @pytest.mark.asyncio
    async def test_create_enhancement_job_active_session_raises(
        self, enhancement_service, mock_db
    ):
        """Test that creating enhancement for active session raises EnhancementError."""
        session = self._make_session(status="active")

        with pytest.raises(EnhancementError, match="session is still active"):
            await enhancement_service.create_enhancement_job(session)

    @pytest.mark.asyncio
    async def test_create_enhancement_job_already_exists_raises(
        self, enhancement_service, mock_db
    ):
        """Test that creating enhancement when one exists raises EnhancementError."""
        existing_job_id = UUID("99999999-9999-9999-9999-999999999999")
        session = self._make_session(enhancement_job_id=existing_job_id)

        with pytest.raises(EnhancementError, match="already has enhancement job"):
            await enhancement_service.create_enhancement_job(session)

    @pytest.mark.asyncio
    async def test_create_enhancement_job_with_options(
        self, enhancement_service, mock_db
    ):
        """Test creating enhancement job with optional features enabled."""
        session = self._make_session()
        mock_job = self._make_job()

        with patch.object(
            enhancement_service.jobs_service, "create_job", return_value=mock_job
        ) as mock_create:
            job = await enhancement_service.create_enhancement_job(
                session,
                enhance_diarization=True,
                enhance_word_timestamps=True,
                enhance_llm_cleanup=True,
                enhance_emotions=True,
            )

        assert job is mock_job
        call_kwargs = mock_create.call_args.kwargs
        params = call_kwargs["parameters"]
        assert params["speaker_detection"] == "diarize"
        assert params["timestamps_granularity"] == "word"
        assert params["llm_cleanup"] is True
        assert params["emotion_detection"] is True

    @pytest.mark.asyncio
    async def test_create_enhancement_job_diarization_disabled(
        self, enhancement_service, mock_db
    ):
        """Test creating enhancement job with diarization disabled."""
        session = self._make_session()
        mock_job = self._make_job()

        with patch.object(
            enhancement_service.jobs_service, "create_job", return_value=mock_job
        ) as mock_create:
            await enhancement_service.create_enhancement_job(
                session,
                enhance_diarization=False,
            )

        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["parameters"]["speaker_detection"] == "none"

    @pytest.mark.asyncio
    async def test_create_enhancement_job_word_timestamps_disabled(
        self, enhancement_service, mock_db
    ):
        """Test creating enhancement job with word timestamps disabled."""
        session = self._make_session()
        mock_job = self._make_job()

        with patch.object(
            enhancement_service.jobs_service, "create_job", return_value=mock_job
        ) as mock_create:
            await enhancement_service.create_enhancement_job(
                session,
                enhance_word_timestamps=False,
            )

        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["parameters"]["timestamps_granularity"] == "segment"


class TestBatchModelMapping:
    """Tests for model mapping from realtime to batch."""

    @pytest.fixture
    def enhancement_service(self):
        """Create EnhancementService instance."""
        return EnhancementService(AsyncMock(), MagicMock())

    def test_fast_model_maps_to_large_v3(self, enhancement_service):
        """Test that 'fast' model maps to whisper-large-v3."""
        result = enhancement_service._get_batch_model("fast")
        assert result == "whisper-large-v3"

    def test_parakeet_model_maps_to_large_v3(self, enhancement_service):
        """Test that parakeet models map to whisper-large-v3."""
        assert enhancement_service._get_batch_model("parakeet") == "whisper-large-v3"
        assert (
            enhancement_service._get_batch_model("parakeet-0.6b") == "whisper-large-v3"
        )
        assert (
            enhancement_service._get_batch_model("parakeet-1.1b") == "whisper-large-v3"
        )

    def test_distil_whisper_maps_to_large_v3(self, enhancement_service):
        """Test that distil-whisper models map to whisper-large-v3."""
        assert (
            enhancement_service._get_batch_model("distil-whisper-large-v3-en")
            == "whisper-large-v3"
        )
        assert (
            enhancement_service._get_batch_model("distil-whisper-large-v2")
            == "whisper-large-v3"
        )

    def test_elevenlabs_scribe_maps_to_large_v3(self, enhancement_service):
        """Test that ElevenLabs scribe models map to whisper-large-v3."""
        assert enhancement_service._get_batch_model("scribe_v1") == "whisper-large-v3"
        assert enhancement_service._get_batch_model("scribe_v2") == "whisper-large-v3"

    def test_unknown_model_defaults_to_large_v3(self, enhancement_service):
        """Test that unknown models default to whisper-large-v3."""
        assert (
            enhancement_service._get_batch_model("unknown-model") == "whisper-large-v3"
        )
        assert enhancement_service._get_batch_model(None) == "whisper-large-v3"

    def test_model_mapping_case_insensitive(self, enhancement_service):
        """Test that model mapping is case-insensitive."""
        assert enhancement_service._get_batch_model("FAST") == "whisper-large-v3"
        assert enhancement_service._get_batch_model("Parakeet") == "whisper-large-v3"


class TestCreateEnhancementForSession:
    """Tests for create_enhancement_for_session convenience function."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock async database session."""
        db = AsyncMock()
        return db

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings."""
        return MagicMock()

    def _make_session(
        self,
        session_id: UUID | None = None,
        audio_uri: str | None = "s3://bucket/audio.wav",
        status: str = "completed",
    ):
        """Create a mock session."""
        session = MagicMock()
        session.id = session_id or UUID("11111111-1111-1111-1111-111111111111")
        session.tenant_id = UUID("00000000-0000-0000-0000-000000000000")
        session.status = status
        session.audio_uri = audio_uri
        session.language = "en"
        session.model = "fast"
        session.engine = "parakeet"
        session.enhancement_job_id = None
        return session

    @pytest.mark.asyncio
    async def test_create_enhancement_for_session_success(self, mock_db, mock_settings):
        """Test creating enhancement for a session by ID."""
        session_id = UUID("11111111-1111-1111-1111-111111111111")
        session = self._make_session(session_id=session_id)
        mock_job = MagicMock()
        mock_job.id = UUID("22222222-2222-2222-2222-222222222222")

        # Mock the select query
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = session
        mock_db.execute.return_value = mock_result

        with patch(
            "dalston.gateway.services.enhancement.EnhancementService"
        ) as MockService:
            mock_service_instance = AsyncMock()
            mock_service_instance.create_enhancement_job.return_value = mock_job
            MockService.return_value = mock_service_instance

            result = await create_enhancement_for_session(
                mock_db, mock_settings, session_id
            )

        assert result is mock_job
        mock_service_instance.create_enhancement_job.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_enhancement_for_session_not_found(
        self, mock_db, mock_settings
    ):
        """Test creating enhancement returns None when session not found."""
        session_id = UUID("11111111-1111-1111-1111-111111111111")

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_result

        result = await create_enhancement_for_session(
            mock_db, mock_settings, session_id
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_create_enhancement_for_session_handles_error(
        self, mock_db, mock_settings
    ):
        """Test creating enhancement returns None on EnhancementError."""
        session_id = UUID("11111111-1111-1111-1111-111111111111")
        session = self._make_session(session_id=session_id, audio_uri=None)

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = session
        mock_db.execute.return_value = mock_result

        with patch(
            "dalston.gateway.services.enhancement.EnhancementService"
        ) as MockService:
            mock_service_instance = AsyncMock()
            mock_service_instance.create_enhancement_job.side_effect = EnhancementError(
                "No audio"
            )
            MockService.return_value = mock_service_instance

            result = await create_enhancement_for_session(
                mock_db, mock_settings, session_id
            )

        assert result is None


class TestEnhancementJobParameters:
    """Tests for enhancement job parameter generation."""

    @pytest.fixture
    def mock_db(self):
        return AsyncMock()

    @pytest.fixture
    def mock_settings(self):
        return MagicMock()

    @pytest.fixture
    def enhancement_service(self, mock_db, mock_settings):
        return EnhancementService(mock_db, mock_settings)

    def _make_session(self, language: str | None = "en", model: str | None = "fast"):
        session = MagicMock()
        session.id = UUID("11111111-1111-1111-1111-111111111111")
        session.tenant_id = UUID("00000000-0000-0000-0000-000000000000")
        session.status = "completed"
        session.audio_uri = "s3://bucket/audio.wav"
        session.language = language
        session.model = model
        session.engine = "parakeet"
        session.enhancement_job_id = None
        return session

    @pytest.mark.asyncio
    async def test_parameters_include_enhancement_metadata(
        self, enhancement_service, mock_db
    ):
        """Test that enhancement job includes metadata about source session."""
        session = self._make_session()
        mock_job = MagicMock()
        mock_job.id = UUID("22222222-2222-2222-2222-222222222222")

        with patch.object(
            enhancement_service.jobs_service, "create_job", return_value=mock_job
        ) as mock_create:
            await enhancement_service.create_enhancement_job(session)

        call_kwargs = mock_create.call_args.kwargs
        params = call_kwargs["parameters"]

        assert "_enhancement" in params
        assert params["_enhancement"]["source_session_id"] == str(session.id)
        assert params["_enhancement"]["original_model"] == "fast"
        assert params["_enhancement"]["original_engine"] == "parakeet"

    @pytest.mark.asyncio
    async def test_parameters_auto_language_when_none(
        self, enhancement_service, mock_db
    ):
        """Test that language defaults to 'auto' when session has none."""
        session = self._make_session(language=None)
        mock_job = MagicMock()
        mock_job.id = UUID("22222222-2222-2222-2222-222222222222")

        with patch.object(
            enhancement_service.jobs_service, "create_job", return_value=mock_job
        ) as mock_create:
            await enhancement_service.create_enhancement_job(session)

        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["parameters"]["language"] == "auto"

    @pytest.mark.asyncio
    async def test_parameters_preserve_session_language(
        self, enhancement_service, mock_db
    ):
        """Test that session language is preserved in enhancement job."""
        session = self._make_session(language="de")
        mock_job = MagicMock()
        mock_job.id = UUID("22222222-2222-2222-2222-222222222222")

        with patch.object(
            enhancement_service.jobs_service, "create_job", return_value=mock_job
        ) as mock_create:
            await enhancement_service.create_enhancement_job(session)

        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs["parameters"]["language"] == "de"
