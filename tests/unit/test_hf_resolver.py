"""Unit tests for HFResolver service (M40.5 HuggingFace Card Routing)."""

from unittest.mock import MagicMock, patch

import pytest

from dalston.gateway.services.hf_resolver import (
    ASR_PIPELINE_TAGS,
    LIBRARY_TO_RUNTIME,
    TAG_TO_RUNTIME,
    HFModelMetadata,
    HFResolver,
)


@pytest.fixture
def resolver() -> HFResolver:
    """Create an HFResolver instance."""
    return HFResolver()


@pytest.fixture
def mock_model_info() -> MagicMock:
    """Create a mock HuggingFace ModelInfo object."""
    info = MagicMock()
    info.library_name = "ctranslate2"
    info.pipeline_tag = "automatic-speech-recognition"
    info.tags = ["whisper", "ctranslate2", "en"]
    info.language = ["en", "es", "fr"]
    info.downloads = 50000
    info.likes = 100
    return info


class TestLibraryToRuntimeMapping:
    """Tests for the LIBRARY_TO_RUNTIME mapping."""

    def test_ctranslate2_maps_to_faster_whisper(self):
        """CTranslate2 library should map to faster-whisper runtime."""
        assert LIBRARY_TO_RUNTIME["ctranslate2"] == "faster-whisper"

    def test_nemo_maps_to_nemo(self):
        """NeMo library should map to nemo runtime."""
        assert LIBRARY_TO_RUNTIME["nemo"] == "nemo"
        assert LIBRARY_TO_RUNTIME["nemo-asr"] == "nemo"

    def test_transformers_maps_to_hf_asr(self):
        """Transformers library should map to hf-asr runtime."""
        assert LIBRARY_TO_RUNTIME["transformers"] == "hf-asr"

    def test_all_mappings_have_valid_runtimes(self):
        """All library mappings should have non-empty runtimes."""
        for library, runtime in LIBRARY_TO_RUNTIME.items():
            assert library, "Library name cannot be empty"
            assert runtime, f"Runtime for {library} cannot be empty"


class TestTagToRuntimeMapping:
    """Tests for the TAG_TO_RUNTIME fallback mapping."""

    def test_faster_whisper_tag_maps_correctly(self):
        """faster-whisper tag should map to faster-whisper runtime."""
        assert TAG_TO_RUNTIME["faster-whisper"] == "faster-whisper"
        assert TAG_TO_RUNTIME["ctranslate2"] == "faster-whisper"

    def test_nemo_tag_maps_correctly(self):
        """nemo tag should map to nemo runtime."""
        assert TAG_TO_RUNTIME["nemo"] == "nemo"

    def test_whisper_tag_defaults_to_faster_whisper(self):
        """Generic whisper tag should default to faster-whisper."""
        assert TAG_TO_RUNTIME["whisper"] == "faster-whisper"


class TestASRPipelineTags:
    """Tests for ASR pipeline tag detection."""

    def test_automatic_speech_recognition_is_asr(self):
        """automatic-speech-recognition should be recognized as ASR."""
        assert "automatic-speech-recognition" in ASR_PIPELINE_TAGS

    def test_audio_to_text_is_asr(self):
        """audio-to-text should be recognized as ASR."""
        assert "audio-to-text" in ASR_PIPELINE_TAGS

    def test_speech_recognition_is_asr(self):
        """speech-recognition should be recognized as ASR."""
        assert "speech-recognition" in ASR_PIPELINE_TAGS


class TestHFResolverGetModelInfo:
    """Tests for HFResolver.get_model_info."""

    @pytest.mark.asyncio
    async def test_get_model_info_success(
        self,
        resolver: HFResolver,
        mock_model_info: MagicMock,
    ):
        """Test successful model info fetch."""
        mock_api = MagicMock()
        mock_api.model_info.return_value = mock_model_info
        resolver._api = mock_api

        result = await resolver.get_model_info("Systran/faster-whisper-large-v3")

        assert result == mock_model_info

    @pytest.mark.asyncio
    async def test_get_model_info_not_found(
        self,
        resolver: HFResolver,
    ):
        """Test model info returns None for missing model."""
        mock_api = MagicMock()
        mock_api.model_info.side_effect = Exception("Model not found")
        resolver._api = mock_api

        result = await resolver.get_model_info("nonexistent/model")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_model_info_api_error(
        self,
        resolver: HFResolver,
    ):
        """Test model info returns None on API error."""
        mock_api = MagicMock()
        mock_api.model_info.side_effect = ConnectionError("API unavailable")
        resolver._api = mock_api

        result = await resolver.get_model_info("some/model")

        assert result is None


class TestHFResolverResolveRuntime:
    """Tests for HFResolver.resolve_runtime."""

    @pytest.mark.asyncio
    async def test_resolve_runtime_by_library_name(
        self,
        resolver: HFResolver,
    ):
        """Test runtime resolution via library_name (priority 1)."""
        mock_info = MagicMock()
        mock_info.library_name = "ctranslate2"
        mock_info.tags = []
        mock_info.pipeline_tag = None

        with patch.object(resolver, "get_model_info", return_value=mock_info):
            result = await resolver.resolve_runtime("Systran/faster-whisper-large-v3")

            assert result == "faster-whisper"

    @pytest.mark.asyncio
    async def test_resolve_runtime_by_library_name_case_insensitive(
        self,
        resolver: HFResolver,
    ):
        """Test library_name matching is case-insensitive."""
        mock_info = MagicMock()
        mock_info.library_name = "CTRANSLATE2"
        mock_info.tags = []
        mock_info.pipeline_tag = None

        with patch.object(resolver, "get_model_info", return_value=mock_info):
            result = await resolver.resolve_runtime("test/model")

            assert result == "faster-whisper"

    @pytest.mark.asyncio
    async def test_resolve_runtime_by_nemo_library(
        self,
        resolver: HFResolver,
    ):
        """Test NeMo library maps correctly."""
        mock_info = MagicMock()
        mock_info.library_name = "nemo"
        mock_info.tags = []
        mock_info.pipeline_tag = None

        with patch.object(resolver, "get_model_info", return_value=mock_info):
            result = await resolver.resolve_runtime("nvidia/parakeet-tdt-1.1b")

            assert result == "nemo"

    @pytest.mark.asyncio
    async def test_resolve_runtime_by_tag_fallback(
        self,
        resolver: HFResolver,
    ):
        """Test runtime resolution via tags when library_name not set."""
        mock_info = MagicMock()
        mock_info.library_name = None
        mock_info.tags = ["automatic-speech-recognition", "nemo", "en"]
        mock_info.pipeline_tag = None

        with patch.object(resolver, "get_model_info", return_value=mock_info):
            result = await resolver.resolve_runtime("some/nemo-model")

            assert result == "nemo"

    @pytest.mark.asyncio
    async def test_resolve_runtime_by_pipeline_tag_fallback(
        self,
        resolver: HFResolver,
    ):
        """Test runtime resolution via pipeline_tag (last resort)."""
        mock_info = MagicMock()
        mock_info.library_name = None
        mock_info.tags = []
        mock_info.pipeline_tag = "automatic-speech-recognition"

        with patch.object(resolver, "get_model_info", return_value=mock_info):
            result = await resolver.resolve_runtime("some/generic-asr-model")

            assert result == "hf-asr"

    @pytest.mark.asyncio
    async def test_resolve_runtime_unknown_returns_none(
        self,
        resolver: HFResolver,
    ):
        """Test unresolvable model returns None."""
        mock_info = MagicMock()
        mock_info.library_name = "unknown-library"
        mock_info.tags = ["unrelated", "tags"]
        mock_info.pipeline_tag = "text-generation"

        with patch.object(resolver, "get_model_info", return_value=mock_info):
            result = await resolver.resolve_runtime("some/llm-model")

            assert result is None

    @pytest.mark.asyncio
    async def test_resolve_runtime_model_not_found(
        self,
        resolver: HFResolver,
    ):
        """Test resolution returns None for missing model."""
        with patch.object(resolver, "get_model_info", return_value=None):
            result = await resolver.resolve_runtime("nonexistent/model")

            assert result is None

    @pytest.mark.asyncio
    async def test_library_name_takes_priority_over_tags(
        self,
        resolver: HFResolver,
    ):
        """Test library_name is preferred over tags."""
        mock_info = MagicMock()
        # library_name says ctranslate2, but tags say nemo
        mock_info.library_name = "ctranslate2"
        mock_info.tags = ["nemo", "nvidia"]
        mock_info.pipeline_tag = "automatic-speech-recognition"

        with patch.object(resolver, "get_model_info", return_value=mock_info):
            result = await resolver.resolve_runtime("test/model")

            # Should use library_name (faster-whisper), not tag (nemo)
            assert result == "faster-whisper"

    @pytest.mark.asyncio
    async def test_transformers_library_requires_asr_pipeline_tag(
        self,
        resolver: HFResolver,
    ):
        """Test transformers library only routes if pipeline_tag is ASR."""
        # LLM with transformers library should NOT route
        mock_info = MagicMock()
        mock_info.library_name = "transformers"
        mock_info.tags = ["llama", "text-generation"]
        mock_info.pipeline_tag = "text-generation"

        with patch.object(resolver, "get_model_info", return_value=mock_info):
            result = await resolver.resolve_runtime("meta-llama/Llama-2-7b")

            assert result is None

    @pytest.mark.asyncio
    async def test_transformers_library_routes_with_asr_pipeline_tag(
        self,
        resolver: HFResolver,
    ):
        """Test transformers library routes correctly for ASR models."""
        mock_info = MagicMock()
        mock_info.library_name = "transformers"
        mock_info.tags = ["whisper", "audio"]
        mock_info.pipeline_tag = "automatic-speech-recognition"

        with patch.object(resolver, "get_model_info", return_value=mock_info):
            result = await resolver.resolve_runtime("openai/whisper-large-v3")

            assert result == "hf-asr"


class TestHFResolverGetModelMetadata:
    """Tests for HFResolver.get_model_metadata."""

    @pytest.mark.asyncio
    async def test_get_model_metadata_success(
        self,
        resolver: HFResolver,
        mock_model_info: MagicMock,
    ):
        """Test successful metadata extraction."""
        with patch.object(resolver, "get_model_info", return_value=mock_model_info):
            with patch.object(
                resolver, "resolve_runtime", return_value="faster-whisper"
            ):
                result = await resolver.get_model_metadata(
                    "Systran/faster-whisper-large-v3"
                )

                assert result is not None
                assert result.model_id == "Systran/faster-whisper-large-v3"
                assert result.library_name == "ctranslate2"
                assert result.pipeline_tag == "automatic-speech-recognition"
                assert result.resolved_runtime == "faster-whisper"
                assert result.downloads == 50000
                assert result.likes == 100
                assert "en" in result.languages

    @pytest.mark.asyncio
    async def test_get_model_metadata_not_found(
        self,
        resolver: HFResolver,
    ):
        """Test metadata returns None for missing model."""
        with patch.object(resolver, "get_model_info", return_value=None):
            result = await resolver.get_model_metadata("nonexistent/model")

            assert result is None

    @pytest.mark.asyncio
    async def test_get_model_metadata_handles_string_language(
        self,
        resolver: HFResolver,
    ):
        """Test language field can be a string or list."""
        mock_info = MagicMock()
        mock_info.library_name = "ctranslate2"
        mock_info.pipeline_tag = "automatic-speech-recognition"
        mock_info.tags = []
        mock_info.language = "en"  # Single string, not list
        mock_info.downloads = 100
        mock_info.likes = 10

        with patch.object(resolver, "get_model_info", return_value=mock_info):
            with patch.object(
                resolver, "resolve_runtime", return_value="faster-whisper"
            ):
                result = await resolver.get_model_metadata("test/model")

                assert result is not None
                assert result.languages == ["en"]

    @pytest.mark.asyncio
    async def test_get_model_metadata_handles_none_language(
        self,
        resolver: HFResolver,
    ):
        """Test missing language field."""
        mock_info = MagicMock()
        mock_info.library_name = "ctranslate2"
        mock_info.pipeline_tag = None
        mock_info.tags = []
        mock_info.language = None
        mock_info.downloads = 0
        mock_info.likes = 0

        with patch.object(resolver, "get_model_info", return_value=mock_info):
            with patch.object(
                resolver, "resolve_runtime", return_value="faster-whisper"
            ):
                result = await resolver.get_model_metadata("test/model")

                assert result is not None
                assert result.languages == []


class TestHFResolverHelperMethods:
    """Tests for HFResolver helper methods."""

    def test_get_library_to_runtime_mapping(
        self,
        resolver: HFResolver,
    ):
        """Test get_library_to_runtime_mapping returns copy."""
        mapping = resolver.get_library_to_runtime_mapping()

        assert mapping == LIBRARY_TO_RUNTIME
        # Verify it's a copy (modifying it doesn't affect original)
        mapping["test"] = "value"
        assert "test" not in LIBRARY_TO_RUNTIME

    def test_get_tag_to_runtime_mapping(
        self,
        resolver: HFResolver,
    ):
        """Test get_tag_to_runtime_mapping returns copy."""
        mapping = resolver.get_tag_to_runtime_mapping()

        assert mapping == TAG_TO_RUNTIME

    def test_get_supported_runtimes(
        self,
        resolver: HFResolver,
    ):
        """Test get_supported_runtimes returns all runtimes."""
        runtimes = resolver.get_supported_runtimes()

        assert "faster-whisper" in runtimes
        assert "nemo" in runtimes
        assert "hf-asr" in runtimes
        # Should be sorted
        assert runtimes == sorted(runtimes)


class TestHFModelMetadata:
    """Tests for HFModelMetadata dataclass."""

    def test_dataclass_fields(self):
        """Test HFModelMetadata has expected fields."""
        metadata = HFModelMetadata(
            model_id="test/model",
            library_name="ctranslate2",
            pipeline_tag="automatic-speech-recognition",
            tags=["whisper"],
            languages=["en"],
            downloads=1000,
            likes=50,
            resolved_runtime="faster-whisper",
        )

        assert metadata.model_id == "test/model"
        assert metadata.library_name == "ctranslate2"
        assert metadata.pipeline_tag == "automatic-speech-recognition"
        assert metadata.tags == ["whisper"]
        assert metadata.languages == ["en"]
        assert metadata.downloads == 1000
        assert metadata.likes == 50
        assert metadata.resolved_runtime == "faster-whisper"

    def test_dataclass_optional_fields(self):
        """Test HFModelMetadata with None values."""
        metadata = HFModelMetadata(
            model_id="test/model",
            library_name=None,
            pipeline_tag=None,
            tags=[],
            languages=[],
            downloads=0,
            likes=0,
            resolved_runtime=None,
        )

        assert metadata.library_name is None
        assert metadata.resolved_runtime is None


class TestHFResolverLazyLoading:
    """Tests for HFResolver lazy API loading."""

    def test_api_not_loaded_on_init(self):
        """Test HfApi is not loaded on initialization."""
        resolver = HFResolver()

        # _api should be None initially
        assert resolver._api is None

    def test_api_loaded_on_first_access(self):
        """Test HfApi is loaded when accessed."""
        with patch("huggingface_hub.HfApi") as mock_hf_api:
            mock_hf_api.return_value = MagicMock()
            resolver = HFResolver()

            # Access the api property
            _ = resolver.api

            mock_hf_api.assert_called_once()

    def test_api_cached_after_first_access(self):
        """Test HfApi is cached after first access."""
        with patch("huggingface_hub.HfApi") as mock_hf_api:
            mock_instance = MagicMock()
            mock_hf_api.return_value = mock_instance
            resolver = HFResolver()

            # Access twice
            api1 = resolver.api
            api2 = resolver.api

            # Should only create once
            mock_hf_api.assert_called_once()
            assert api1 is api2
