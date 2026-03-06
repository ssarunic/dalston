"""Tests for M31 capability-driven engine selection."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dalston.engine_sdk.types import EngineCapabilities
from dalston.orchestrator.catalog import CatalogEntry, EngineCatalog
from dalston.orchestrator.engine_selector import (
    EngineSelectionResult,
    ModelSelectionError,
    NoCapableEngineError,
    NoDownloadedModelError,
    _meets_requirements,
    _rank_and_select,
    _should_add_alignment,
    _should_add_diarization,
    extract_requirements,
    select_engine,
    select_pipeline_engines,
)
from dalston.orchestrator.registry import BatchEngineState

# =============================================================================
# Fixtures
# =============================================================================


def make_capabilities(
    runtime: str = "test-engine",
    languages: list[str] | None = None,
    supports_word_timestamps: bool = False,
    includes_diarization: bool = False,
    supports_streaming: bool = False,
    rtf_gpu: float | None = None,
) -> EngineCapabilities:
    """Create EngineCapabilities for testing."""
    return EngineCapabilities(
        runtime=runtime,
        version="1.0.0",
        stages=["transcribe"],
        languages=languages,
        supports_word_timestamps=supports_word_timestamps,
        includes_diarization=includes_diarization,
        supports_streaming=supports_streaming,
        rtf_gpu=rtf_gpu,
    )


def make_engine_state(
    runtime: str = "test-engine",
    instance: str | None = None,
    stage: str = "transcribe",
    capabilities: EngineCapabilities | None = None,
    is_available: bool = True,
) -> BatchEngineState:
    """Create BatchEngineState for testing."""
    now = datetime.now(UTC)
    state = BatchEngineState(
        runtime=runtime,
        instance=instance or f"{runtime}-abc123",
        stage=stage,
        stream_name=f"dalston:stream:{runtime}",
        status="idle" if is_available else "offline",
        current_task=None,
        last_heartbeat=now,
        registered_at=now,
        capabilities=capabilities,
    )
    return state


def make_catalog_entry(
    runtime: str = "test-engine",
    languages: list[str] | None = None,
) -> CatalogEntry:
    """Create CatalogEntry for testing."""
    return CatalogEntry(
        runtime=runtime,
        image=f"dalston/{runtime}:latest",
        capabilities=make_capabilities(runtime=runtime, languages=languages),
    )


class _ScalarOneResult:
    """Minimal SQLAlchemy-like result wrapper for scalar_one_or_none()."""

    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


# =============================================================================
# Test extract_requirements
# =============================================================================


class TestExtractRequirements:
    def test_extracts_language(self):
        params = {"language": "en"}
        reqs = extract_requirements(params)
        assert reqs == {"language": "en"}

    def test_extracts_language_code_alias(self):
        params = {"language_code": "fr"}
        reqs = extract_requirements(params)
        assert reqs == {"language": "fr"}

    def test_ignores_auto_language(self):
        params = {"language": "auto"}
        reqs = extract_requirements(params)
        assert reqs == {}

    def test_extracts_streaming(self):
        params = {"streaming": True}
        reqs = extract_requirements(params)
        assert reqs == {"streaming": True}

    def test_empty_params_returns_empty(self):
        reqs = extract_requirements({})
        assert reqs == {}


# =============================================================================
# Test _meets_requirements
# =============================================================================


class TestMeetsRequirements:
    def test_no_requirements_always_matches(self):
        caps = make_capabilities(languages=["en"])
        assert _meets_requirements(caps, {}) is True

    def test_language_matches(self):
        caps = make_capabilities(languages=["en", "es"])
        assert _meets_requirements(caps, {"language": "en"}) is True
        assert _meets_requirements(caps, {"language": "ES"}) is True  # Case insensitive

    def test_language_not_supported(self):
        caps = make_capabilities(languages=["en"])
        assert _meets_requirements(caps, {"language": "hr"}) is False

    def test_null_languages_means_all(self):
        caps = make_capabilities(languages=None)  # All languages
        assert _meets_requirements(caps, {"language": "hr"}) is True
        assert _meets_requirements(caps, {"language": "zh"}) is True

    def test_streaming_required_but_not_supported(self):
        caps = make_capabilities(supports_streaming=False)
        assert _meets_requirements(caps, {"streaming": True}) is False

    def test_streaming_required_and_supported(self):
        caps = make_capabilities(supports_streaming=True)
        assert _meets_requirements(caps, {"streaming": True}) is True


# =============================================================================
# Test _rank_and_select
# =============================================================================


class TestRankAndSelect:
    def test_prefers_native_word_timestamps(self):
        engines = [
            make_engine_state(
                "slow",
                capabilities=make_capabilities("slow", supports_word_timestamps=False),
            ),
            make_engine_state(
                "fast",
                capabilities=make_capabilities("fast", supports_word_timestamps=True),
            ),
        ]
        result = _rank_and_select(engines, {})
        assert result.runtime == "fast"
        assert "native word timestamps" in result.selection_reason

    def test_prefers_native_diarization(self):
        engines = [
            make_engine_state(
                "no-diar",
                capabilities=make_capabilities("no-diar", includes_diarization=False),
            ),
            make_engine_state(
                "has-diar",
                capabilities=make_capabilities("has-diar", includes_diarization=True),
            ),
        ]
        result = _rank_and_select(engines, {})
        assert result.runtime == "has-diar"
        assert "native diarization" in result.selection_reason

    def test_prefers_faster_rtf(self):
        engines = [
            make_engine_state(
                "slow",
                capabilities=make_capabilities("slow", rtf_gpu=0.1),
            ),
            make_engine_state(
                "fast",
                capabilities=make_capabilities("fast", rtf_gpu=0.01),
            ),
        ]
        result = _rank_and_select(engines, {})
        assert result.runtime == "fast"

    def test_prefers_language_specific_over_universal(self):
        """When language is specified, prefer language-specific engines."""
        engines = [
            make_engine_state(
                "universal",
                capabilities=make_capabilities("universal", languages=None),
            ),
            make_engine_state(
                "english-only",
                capabilities=make_capabilities("english-only", languages=["en"]),
            ),
        ]
        # Specify English - should prefer the English-specific engine
        result = _rank_and_select(engines, {"language": "en"})
        assert result.runtime == "english-only"

    def test_prefers_universal_for_auto_detection(self):
        """When no language specified (auto), prefer universal engines for safety."""
        engines = [
            make_engine_state(
                "universal",
                capabilities=make_capabilities("universal", languages=None),
            ),
            make_engine_state(
                "english-only",
                capabilities=make_capabilities("english-only", languages=["en"]),
            ),
        ]
        # No language specified - should prefer universal for safety
        result = _rank_and_select(engines, {})
        assert result.runtime == "universal"


# =============================================================================
# Test select_engine
# =============================================================================


class TestSelectEngine:
    @pytest.fixture
    def mock_registry(self):
        registry = AsyncMock()
        return registry

    @pytest.fixture
    def mock_catalog(self):
        catalog = MagicMock(spec=EngineCatalog)
        catalog.find_engines.return_value = []
        return catalog

    @pytest.mark.asyncio
    async def test_single_capable_engine_selected(self, mock_registry, mock_catalog):
        caps = make_capabilities("only-one", languages=["en"])
        engine = make_engine_state("only-one", capabilities=caps)

        mock_registry.get_engines_for_stage.return_value = [engine]

        result = await select_engine(
            "transcribe", {"language": "en"}, mock_registry, mock_catalog
        )

        assert result.runtime == "only-one"
        assert result.selection_reason == "only capable engine"

    @pytest.mark.asyncio
    async def test_no_downloaded_model_error_reports_attempted_runtimes(
        self, mock_registry, mock_catalog, monkeypatch
    ):
        runtime_a_caps = make_capabilities("runtime-a")
        runtime_b_caps = make_capabilities("runtime-b")
        runtime_a = make_engine_state(
            "runtime-a", stage="align", capabilities=runtime_a_caps
        )
        runtime_b = make_engine_state(
            "runtime-b", stage="align", capabilities=runtime_b_caps
        )

        mock_registry.get_engines_for_stage.return_value = [runtime_a, runtime_b]

        async def _mock_find_best_downloaded_model(*args, **kwargs):
            return None

        monkeypatch.setattr(
            "dalston.orchestrator.engine_selector._find_best_downloaded_model",
            _mock_find_best_downloaded_model,
        )

        with pytest.raises(NoDownloadedModelError) as exc_info:
            await select_engine(
                "align",
                {},
                mock_registry,
                mock_catalog,
                db=AsyncMock(),
            )

        error = exc_info.value
        assert error.runtime == "runtime-a"
        assert error.attempted_runtimes == ["runtime-a", "runtime-b"]
        assert "Attempted runtimes: runtime-a, runtime-b." in str(error)

    @pytest.mark.asyncio
    async def test_raises_when_no_capable_engine(self, mock_registry, mock_catalog):
        # Engine only supports English
        caps = make_capabilities("parakeet", languages=["en"])
        engine = make_engine_state("parakeet", capabilities=caps)

        mock_registry.get_engines_for_stage.return_value = [engine]
        mock_catalog.find_engines.return_value = [
            make_catalog_entry("faster-whisper", languages=None)
        ]

        with pytest.raises(NoCapableEngineError) as exc_info:
            await select_engine(
                "transcribe", {"language": "hr"}, mock_registry, mock_catalog
            )

        assert exc_info.value.stage == "transcribe"
        assert "parakeet" in str(exc_info.value)
        assert len(exc_info.value.catalog_alternatives) == 1

    @pytest.mark.asyncio
    async def test_user_preference_validated(self, mock_registry, mock_catalog):
        caps = make_capabilities("preferred", languages=["en"])
        engine = make_engine_state("preferred", capabilities=caps)

        mock_registry.get_engine.return_value = engine

        result = await select_engine(
            "transcribe",
            {"language": "en"},
            mock_registry,
            mock_catalog,
            user_preference="preferred",
        )

        assert result.runtime == "preferred"
        assert result.selection_reason == "user preference"

    @pytest.mark.asyncio
    async def test_user_preference_rejects_incapable(self, mock_registry, mock_catalog):
        caps = make_capabilities("english-only", languages=["en"])
        engine = make_engine_state("english-only", capabilities=caps)

        mock_registry.get_engine.return_value = engine
        mock_catalog.find_engines.return_value = []

        with pytest.raises(NoCapableEngineError):
            await select_engine(
                "transcribe",
                {"language": "hr"},
                mock_registry,
                mock_catalog,
                user_preference="english-only",
            )

    @pytest.mark.asyncio
    async def test_stage_model_preference_success(self, mock_registry, mock_catalog):
        db_model = SimpleNamespace(
            id="pyannote/speaker-diarization-community-1",
            stage="diarize",
            status="ready",
            runtime="pyannote-4.0",
            runtime_model_id="pyannote/speaker-diarization-community-1",
            source="pyannote/speaker-diarization-community-1",
            languages=None,
        )
        mock_db = AsyncMock()
        mock_db.execute.return_value = _ScalarOneResult(db_model)

        runtime = make_engine_state("pyannote-4.0", stage="diarize")
        mock_registry.get_engine.return_value = runtime

        result = await select_engine(
            "diarize",
            {},
            mock_registry,
            mock_catalog,
            user_preference="pyannote/speaker-diarization-community-1",
            db=mock_db,
            user_preference_is_model=True,
        )

        assert result.runtime == "pyannote-4.0"
        # Non-transcribe stages use explicit runtime_model_id from registry.
        assert result.runtime_model_id == "pyannote/speaker-diarization-community-1"

    @pytest.mark.asyncio
    async def test_stage_model_not_found(self, mock_registry, mock_catalog):
        mock_db = AsyncMock()
        mock_db.execute.return_value = _ScalarOneResult(None)

        with pytest.raises(ModelSelectionError) as exc_info:
            await select_engine(
                "align",
                {},
                mock_registry,
                mock_catalog,
                user_preference="missing-align-model",
                db=mock_db,
                user_preference_is_model=True,
            )

        assert exc_info.value.code == "model_not_found"
        assert exc_info.value.stage == "align"

    @pytest.mark.asyncio
    async def test_stage_model_stage_mismatch(self, mock_registry, mock_catalog):
        db_model = SimpleNamespace(
            id="urchade/gliner_multi-v2.1",
            stage="pii_detect",
            status="ready",
            runtime="pii-presidio",
            runtime_model_id="urchade/gliner_multi-v2.1",
            source="urchade/gliner_multi-v2.1",
            languages=None,
        )
        mock_db = AsyncMock()
        mock_db.execute.return_value = _ScalarOneResult(db_model)

        with pytest.raises(ModelSelectionError) as exc_info:
            await select_engine(
                "align",
                {},
                mock_registry,
                mock_catalog,
                user_preference="urchade/gliner_multi-v2.1",
                db=mock_db,
                user_preference_is_model=True,
            )

        assert exc_info.value.code == "model_stage_mismatch"

    @pytest.mark.asyncio
    async def test_stage_model_not_ready(self, mock_registry, mock_catalog):
        db_model = SimpleNamespace(
            id="jonatasgrosman/wav2vec2-large-xlsr-53-japanese",
            stage="align",
            status="not_downloaded",
            runtime="phoneme-align",
            runtime_model_id="jonatasgrosman/wav2vec2-large-xlsr-53-japanese",
            source="jonatasgrosman/wav2vec2-large-xlsr-53-japanese",
            languages=["ja"],
        )
        mock_db = AsyncMock()
        mock_db.execute.return_value = _ScalarOneResult(db_model)

        with pytest.raises(ModelSelectionError) as exc_info:
            await select_engine(
                "align",
                {},
                mock_registry,
                mock_catalog,
                user_preference="jonatasgrosman/wav2vec2-large-xlsr-53-japanese",
                db=mock_db,
                user_preference_is_model=True,
            )

        assert exc_info.value.code == "model_not_ready"

    @pytest.mark.asyncio
    async def test_stage_model_runtime_unavailable(self, mock_registry, mock_catalog):
        db_model = SimpleNamespace(
            id="urchade/gliner_multi-v2.1",
            stage="pii_detect",
            status="ready",
            runtime="pii-presidio",
            runtime_model_id="urchade/gliner_multi-v2.1",
            source="urchade/gliner_multi-v2.1",
            languages=None,
        )
        mock_db = AsyncMock()
        mock_db.execute.return_value = _ScalarOneResult(db_model)
        mock_registry.get_engine.return_value = None

        with pytest.raises(ModelSelectionError) as exc_info:
            await select_engine(
                "pii_detect",
                {},
                mock_registry,
                mock_catalog,
                user_preference="urchade/gliner_multi-v2.1",
                db=mock_db,
                user_preference_is_model=True,
            )

        assert exc_info.value.code == "runtime_unavailable"


# =============================================================================
# Test _should_add_alignment and _should_add_diarization
# =============================================================================


class TestShouldAddAlignment:
    def test_needs_alignment_when_no_native_timestamps(self):
        selection = EngineSelectionResult(
            runtime="faster-whisper",
            capabilities=make_capabilities(supports_word_timestamps=False),
            selection_reason="test",
        )
        assert _should_add_alignment({}, selection) is True

    def test_skip_alignment_when_native_timestamps(self):
        selection = EngineSelectionResult(
            runtime="parakeet",
            capabilities=make_capabilities(supports_word_timestamps=True),
            selection_reason="test",
        )
        assert _should_add_alignment({}, selection) is False

    def test_skip_alignment_when_disabled_by_params(self):
        selection = EngineSelectionResult(
            runtime="faster-whisper",
            capabilities=make_capabilities(supports_word_timestamps=False),
            selection_reason="test",
        )
        assert _should_add_alignment({"word_timestamps": False}, selection) is False

    def test_skip_alignment_when_segment_granularity(self):
        selection = EngineSelectionResult(
            runtime="faster-whisper",
            capabilities=make_capabilities(supports_word_timestamps=False),
            selection_reason="test",
        )
        assert (
            _should_add_alignment({"timestamps_granularity": "segment"}, selection)
            is False
        )


class TestShouldAddDiarization:
    def test_needs_diarization_when_requested_no_native(self):
        selection = EngineSelectionResult(
            runtime="faster-whisper",
            capabilities=make_capabilities(includes_diarization=False),
            selection_reason="test",
        )
        assert (
            _should_add_diarization({"speaker_detection": "diarize"}, selection) is True
        )

    def test_skip_diarization_when_native(self):
        selection = EngineSelectionResult(
            runtime="whisperx-full",
            capabilities=make_capabilities(includes_diarization=True),
            selection_reason="test",
        )
        assert (
            _should_add_diarization({"speaker_detection": "diarize"}, selection)
            is False
        )

    def test_skip_diarization_when_not_requested(self):
        selection = EngineSelectionResult(
            runtime="faster-whisper",
            capabilities=make_capabilities(includes_diarization=False),
            selection_reason="test",
        )
        assert _should_add_diarization({}, selection) is False


# =============================================================================
# Test NoCapableEngineError
# =============================================================================


class TestNoCapableEngineError:
    def test_message_includes_stage(self):
        err = NoCapableEngineError(
            stage="transcribe",
            requirements={"language": "hr"},
            candidates=[],
            catalog_alternatives=[],
        )
        assert "transcribe" in str(err)

    def test_message_explains_mismatch(self):
        caps = make_capabilities("parakeet", languages=["en"])
        engine = make_engine_state("parakeet", capabilities=caps)

        err = NoCapableEngineError(
            stage="transcribe",
            requirements={"language": "hr"},
            candidates=[engine],
            catalog_alternatives=[],
        )

        assert "parakeet" in str(err)
        assert "hr" in str(err)

    def test_to_dict_structure(self):
        caps = make_capabilities("parakeet", languages=["en"])
        engine = make_engine_state("parakeet", capabilities=caps)
        alt = make_catalog_entry("faster-whisper", languages=None)

        err = NoCapableEngineError(
            stage="transcribe",
            requirements={"language": "hr"},
            candidates=[engine],
            catalog_alternatives=[alt],
        )

        d = err.to_dict()
        assert d["error"] == "no_capable_engine"
        assert d["stage"] == "transcribe"
        assert d["requirements"] == {"language": "hr"}
        assert len(d["running_engines"]) == 1
        assert d["running_engines"][0]["id"] == "parakeet"
        assert len(d["catalog_alternatives"]) == 1
        assert d["catalog_alternatives"][0]["id"] == "faster-whisper"


# =============================================================================
# Test select_pipeline_engines
# =============================================================================


class TestSelectPipelineEngines:
    @pytest.fixture
    def mock_registry(self):
        registry = AsyncMock()
        return registry

    @pytest.fixture
    def mock_catalog(self):
        catalog = MagicMock(spec=EngineCatalog)
        catalog.find_engines.return_value = []
        return catalog

    @pytest.mark.asyncio
    async def test_selects_all_required_stages(self, mock_registry, mock_catalog):
        # Setup engines for each stage
        async def get_engines_for_stage(stage: str):
            stages_caps = {
                "prepare": make_capabilities("audio-prepare"),
                "transcribe": make_capabilities(
                    "faster-whisper", supports_word_timestamps=False
                ),
                "align": make_capabilities("phoneme-align"),
                "merge": make_capabilities("final-merger"),
            }
            caps = stages_caps.get(stage)
            if caps:
                caps.stages = [stage]
                return [
                    make_engine_state(
                        runtime=caps.runtime, stage=stage, capabilities=caps
                    )
                ]
            return []

        mock_registry.get_engines_for_stage.side_effect = get_engines_for_stage

        selection = await select_pipeline_engines({}, mock_registry, mock_catalog)
        selections = selection.stages

        assert "prepare" in selections
        assert "transcribe" in selections
        assert (
            "align" in selections
        )  # Needed because faster-whisper lacks native timestamps
        assert "merge" in selections

    @pytest.mark.asyncio
    async def test_skips_alignment_with_native_timestamps(
        self, mock_registry, mock_catalog
    ):
        async def get_engines_for_stage(stage: str):
            stages_caps = {
                "prepare": make_capabilities("audio-prepare"),
                "transcribe": make_capabilities(
                    "parakeet", languages=["en"], supports_word_timestamps=True
                ),
                "merge": make_capabilities("final-merger"),
            }
            caps = stages_caps.get(stage)
            if caps:
                caps.stages = [stage]
                return [
                    make_engine_state(
                        runtime=caps.runtime, stage=stage, capabilities=caps
                    )
                ]
            return []

        mock_registry.get_engines_for_stage.side_effect = get_engines_for_stage

        selection = await select_pipeline_engines(
            {"language": "en"}, mock_registry, mock_catalog
        )
        selections = selection.stages

        assert "prepare" in selections
        assert "transcribe" in selections
        assert "align" not in selections  # Parakeet has native timestamps
        assert "merge" in selections

    @pytest.mark.asyncio
    async def test_adds_diarization_when_requested(self, mock_registry, mock_catalog):
        async def get_engines_for_stage(stage: str):
            stages_caps = {
                "prepare": make_capabilities("audio-prepare"),
                "transcribe": make_capabilities("faster-whisper"),
                "align": make_capabilities("phoneme-align"),
                "diarize": make_capabilities("pyannote-4.0"),
                "merge": make_capabilities("final-merger"),
            }
            caps = stages_caps.get(stage)
            if caps:
                caps.stages = [stage]
                return [
                    make_engine_state(
                        runtime=caps.runtime, stage=stage, capabilities=caps
                    )
                ]
            return []

        mock_registry.get_engines_for_stage.side_effect = get_engines_for_stage

        selection = await select_pipeline_engines(
            {"speaker_detection": "diarize"}, mock_registry, mock_catalog
        )
        selections = selection.stages

        assert "diarize" in selections
        assert selections["diarize"].runtime == "pyannote-4.0"

    @pytest.mark.asyncio
    async def test_falls_back_to_segment_when_align_model_not_downloaded_default(
        self, mock_registry, mock_catalog
    ):
        parameters: dict = {}

        with patch(
            "dalston.orchestrator.engine_selector.select_engine", new_callable=AsyncMock
        ) as mock_select_engine:
            mock_select_engine.side_effect = [
                EngineSelectionResult(
                    runtime="audio-prepare",
                    capabilities=make_capabilities("audio-prepare"),
                    selection_reason="prepare",
                ),
                EngineSelectionResult(
                    runtime="faster-whisper",
                    capabilities=make_capabilities(
                        "faster-whisper", supports_word_timestamps=False
                    ),
                    selection_reason="transcribe",
                ),
                NoDownloadedModelError(runtime="phoneme-align", stage="align"),
                EngineSelectionResult(
                    runtime="final-merger",
                    capabilities=make_capabilities("final-merger"),
                    selection_reason="merge",
                ),
            ]

            selection = await select_pipeline_engines(
                parameters, mock_registry, mock_catalog
            )
            selections = selection.stages

        assert "align" not in selections
        assert selections["transcribe"].runtime == "faster-whisper"
        assert selections["merge"].runtime == "final-merger"
        assert "timestamps_granularity" not in parameters
        assert selection.effective_parameters["timestamps_granularity"] == "segment"
        assert selection.effective_parameters["word_timestamps"] is False

    @pytest.mark.asyncio
    async def test_falls_back_to_segment_when_word_timestamps_requested(
        self, mock_registry, mock_catalog
    ):
        parameters = {"timestamps_granularity": "word"}

        with patch(
            "dalston.orchestrator.engine_selector.select_engine", new_callable=AsyncMock
        ) as mock_select_engine:
            mock_select_engine.side_effect = [
                EngineSelectionResult(
                    runtime="audio-prepare",
                    capabilities=make_capabilities("audio-prepare"),
                    selection_reason="prepare",
                ),
                EngineSelectionResult(
                    runtime="faster-whisper",
                    capabilities=make_capabilities(
                        "faster-whisper", supports_word_timestamps=False
                    ),
                    selection_reason="transcribe",
                ),
                NoDownloadedModelError(runtime="phoneme-align", stage="align"),
                EngineSelectionResult(
                    runtime="final-merger",
                    capabilities=make_capabilities("final-merger"),
                    selection_reason="merge",
                ),
            ]

            selection = await select_pipeline_engines(
                parameters, mock_registry, mock_catalog
            )
            selections = selection.stages

        assert "align" not in selections
        assert parameters["timestamps_granularity"] == "word"
        assert "word_timestamps" not in parameters
        assert selection.effective_parameters["timestamps_granularity"] == "segment"
        assert selection.effective_parameters["word_timestamps"] is False

    @pytest.mark.asyncio
    async def test_falls_back_to_segment_when_no_align_engine_and_no_pin(
        self, mock_registry, mock_catalog
    ):
        parameters = {"timestamps_granularity": "word"}

        with patch(
            "dalston.orchestrator.engine_selector.select_engine", new_callable=AsyncMock
        ) as mock_select_engine:
            mock_select_engine.side_effect = [
                EngineSelectionResult(
                    runtime="audio-prepare",
                    capabilities=make_capabilities("audio-prepare"),
                    selection_reason="prepare",
                ),
                EngineSelectionResult(
                    runtime="faster-whisper",
                    capabilities=make_capabilities(
                        "faster-whisper", supports_word_timestamps=False
                    ),
                    selection_reason="transcribe",
                ),
                NoCapableEngineError(
                    stage="align",
                    requirements={},
                    candidates=[],
                    catalog_alternatives=[],
                ),
                EngineSelectionResult(
                    runtime="final-merger",
                    capabilities=make_capabilities("final-merger"),
                    selection_reason="merge",
                ),
            ]

            selection = await select_pipeline_engines(
                parameters, mock_registry, mock_catalog
            )
            selections = selection.stages

        assert "align" not in selections
        assert parameters["timestamps_granularity"] == "word"
        assert selection.effective_parameters["timestamps_granularity"] == "segment"
        assert selection.effective_parameters["word_timestamps"] is False

    @pytest.mark.asyncio
    async def test_keeps_pinned_align_model_request_strict(
        self, mock_registry, mock_catalog
    ):
        parameters = {"model_align": "facebook/wav2vec2-base-960h"}

        with patch(
            "dalston.orchestrator.engine_selector.select_engine", new_callable=AsyncMock
        ) as mock_select_engine:
            mock_select_engine.side_effect = [
                EngineSelectionResult(
                    runtime="audio-prepare",
                    capabilities=make_capabilities("audio-prepare"),
                    selection_reason="prepare",
                ),
                EngineSelectionResult(
                    runtime="faster-whisper",
                    capabilities=make_capabilities(
                        "faster-whisper", supports_word_timestamps=False
                    ),
                    selection_reason="transcribe",
                ),
                NoDownloadedModelError(runtime="phoneme-align", stage="align"),
            ]

            with pytest.raises(NoDownloadedModelError):
                await select_pipeline_engines(parameters, mock_registry, mock_catalog)
