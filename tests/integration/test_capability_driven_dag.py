"""Integration tests for M31 capability-driven DAG building.

Tests that build_task_dag correctly adapts DAG shape based on
engine capabilities (native word timestamps, native diarization).
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from dalston.common.registry import EngineRecord
from dalston.engine_sdk.types import EngineCapabilities
from dalston.orchestrator.catalog import CatalogEntry, EngineCatalog
from dalston.orchestrator.dag import build_task_dag
from dalston.orchestrator.engine_selector import (
    ModelSelectionError,
    NoCapableEngineError,
)

# =============================================================================
# Test Fixtures
# =============================================================================


def make_capabilities(
    runtime: str,
    stage: str = "transcribe",
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
        stages=[stage],
        languages=languages,
        supports_word_timestamps=supports_word_timestamps,
        includes_diarization=includes_diarization,
        supports_streaming=supports_streaming,
        rtf_gpu=rtf_gpu,
    )


def make_engine_state(
    runtime: str,
    stage: str,
    capabilities: EngineCapabilities | None = None,
    is_available: bool = True,
) -> EngineRecord:
    """Create EngineRecord for testing."""
    now = datetime.now(UTC)
    return EngineRecord(
        runtime=runtime,
        instance=f"{runtime}-test-instance",
        stage=stage,
        interfaces=["batch"],
        stream_name=f"dalston:stream:{runtime}",
        status="idle" if is_available else "offline",
        last_heartbeat=now,
        registered_at=now,
        capabilities=capabilities,
    )


def make_catalog_entry(
    runtime: str,
    stage: str = "transcribe",
    languages: list[str] | None = None,
) -> CatalogEntry:
    """Create CatalogEntry for testing."""
    return CatalogEntry(
        runtime=runtime,
        image=f"dalston/{runtime}:latest",
        capabilities=make_capabilities(
            runtime=runtime, stage=stage, languages=languages
        ),
    )


@pytest.fixture
def job_id():
    return uuid4()


@pytest.fixture
def audio_uri():
    return "s3://test-bucket/audio/test.wav"


@pytest.fixture
def mock_catalog():
    """Mock catalog that returns empty alternatives."""
    catalog = MagicMock(spec=EngineCatalog)
    catalog.find_engines.return_value = []
    return catalog


def create_mock_registry(engine_configs: dict[str, dict]) -> AsyncMock:
    """Create a mock registry with the specified engine configurations.

    Args:
        engine_configs: Dict mapping stage to engine config dict with keys:
            - runtime: str
            - capabilities: EngineCapabilities or dict with capability options

    Returns:
        Mock UnifiedEngineRegistry
    """
    registry = AsyncMock()

    engines_by_stage: dict[str, list[EngineRecord]] = {}

    for stage, config in engine_configs.items():
        runtime = config["runtime"]
        caps_config = config.get("capabilities", {})

        if isinstance(caps_config, EngineCapabilities):
            caps = caps_config
        else:
            caps = make_capabilities(
                runtime=runtime,
                stage=stage,
                **caps_config,
            )

        engine = make_engine_state(runtime, stage, caps)
        engines_by_stage.setdefault(stage, []).append(engine)

    async def get_by_stage(stage: str):
        return engines_by_stage.get(stage, [])

    registry.get_by_stage.side_effect = get_by_stage

    return registry


class _ScalarOneResult:
    """Minimal SQLAlchemy-like result wrapper for scalar_one_or_none()."""

    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


# =============================================================================
# Test DAG Shape Adaptation
# =============================================================================


class TestDagShapeWithNativeWordTimestamps:
    """Tests that DAG skips alignment when transcriber has native word timestamps."""

    @pytest.mark.asyncio
    async def test_skips_alignment_with_native_timestamps(
        self, job_id, audio_uri, mock_catalog
    ):
        """Transcriber with native word timestamps -> no align stage."""
        registry = create_mock_registry(
            {
                "prepare": {"runtime": "audio-prepare"},
                "transcribe": {
                    "runtime": "parakeet",
                    "capabilities": {
                        "supports_word_timestamps": True,
                        "languages": ["en"],
                    },
                },
            }
        )

        tasks = await build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"language": "en"},
            registry=registry,
            catalog=mock_catalog,
        )

        stages = [t.stage for t in tasks]
        assert "prepare" in stages
        assert "transcribe" in stages
        assert "align" not in stages  # Skipped due to native support
        assert "merge" not in stages
        assert len(tasks) == 2  # prepare, transcribe

    @pytest.mark.asyncio
    async def test_includes_alignment_without_native_timestamps(
        self, job_id, audio_uri, mock_catalog
    ):
        """Transcriber without native timestamps -> align stage included."""
        registry = create_mock_registry(
            {
                "prepare": {"runtime": "audio-prepare"},
                "transcribe": {
                    "runtime": "faster-whisper",
                    "capabilities": {"supports_word_timestamps": False},
                },
                "align": {"runtime": "phoneme-align"},
            }
        )

        tasks = await build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={},
            registry=registry,
            catalog=mock_catalog,
        )

        stages = [t.stage for t in tasks]
        assert "align" in stages  # Included because transcriber lacks native support
        assert "merge" not in stages
        assert len(tasks) == 3  # prepare, transcribe, align


class TestDagShapeWithNativeDiarization:
    """Tests that DAG skips diarization when transcriber has native diarization."""

    @pytest.mark.asyncio
    async def test_skips_diarization_with_native_support(
        self, job_id, audio_uri, mock_catalog
    ):
        """Transcriber with native diarization -> no diarize stage even if requested."""
        registry = create_mock_registry(
            {
                "prepare": {"runtime": "audio-prepare"},
                "transcribe": {
                    "runtime": "whisperx-full",
                    "capabilities": {
                        "supports_word_timestamps": True,
                        "includes_diarization": True,
                    },
                },
            }
        )

        tasks = await build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "diarize"},
            registry=registry,
            catalog=mock_catalog,
        )

        stages = [t.stage for t in tasks]
        assert "diarize" not in stages  # Skipped - transcriber has native diarization
        assert "align" not in stages  # Skipped - transcriber has native timestamps
        assert "merge" not in stages
        assert len(tasks) == 2  # prepare, transcribe

    @pytest.mark.asyncio
    async def test_includes_diarization_without_native_support(
        self, job_id, audio_uri, mock_catalog
    ):
        """Transcriber without native diarization -> diarize stage included when requested."""
        registry = create_mock_registry(
            {
                "prepare": {"runtime": "audio-prepare"},
                "transcribe": {
                    "runtime": "faster-whisper",
                    "capabilities": {
                        "supports_word_timestamps": False,
                        "includes_diarization": False,
                    },
                },
                "align": {"runtime": "phoneme-align"},
                "diarize": {"runtime": "pyannote-4.0"},
            }
        )

        tasks = await build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "diarize"},
            registry=registry,
            catalog=mock_catalog,
        )

        stages = [t.stage for t in tasks]
        assert "diarize" in stages  # Included when requested
        assert "align" in stages
        assert "merge" not in stages
        assert len(tasks) == 4  # prepare, transcribe, align, diarize


class TestDagShapeWithLanguageRequirements:
    """Tests DAG building with language requirements."""

    @pytest.mark.asyncio
    async def test_selects_language_capable_engine(
        self, job_id, audio_uri, mock_catalog
    ):
        """Engine supporting requested language is selected."""
        registry = create_mock_registry(
            {
                "prepare": {"runtime": "audio-prepare"},
                "transcribe": {
                    "runtime": "faster-whisper",
                    "capabilities": {"languages": None},  # Universal
                },
                "align": {"runtime": "phoneme-align"},
            }
        )

        tasks = await build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"language": "hr"},  # Croatian
            registry=registry,
            catalog=mock_catalog,
        )

        by_stage = {t.stage: t for t in tasks}
        assert by_stage["transcribe"].runtime == "faster-whisper"

    @pytest.mark.asyncio
    async def test_raises_error_when_no_engine_supports_language(
        self, job_id, audio_uri, mock_catalog
    ):
        """NoCapableEngineError raised when no engine supports the language."""
        registry = create_mock_registry(
            {
                "prepare": {"runtime": "audio-prepare"},
                "transcribe": {
                    "runtime": "parakeet",
                    "capabilities": {"languages": ["en"]},  # English only
                },
            }
        )

        with pytest.raises(NoCapableEngineError) as exc_info:
            await build_task_dag(
                job_id=job_id,
                audio_uri=audio_uri,
                parameters={"language": "hr"},  # Croatian - not supported
                registry=registry,
                catalog=mock_catalog,
            )

        assert exc_info.value.stage == "transcribe"
        assert "hr" in str(exc_info.value)


class TestDagWithTimestampGranularity:
    """Tests DAG building with different timestamp granularity settings."""

    @pytest.mark.asyncio
    async def test_segment_granularity_skips_alignment(
        self, job_id, audio_uri, mock_catalog
    ):
        """timestamps_granularity=segment skips alignment even without native timestamps."""
        registry = create_mock_registry(
            {
                "prepare": {"runtime": "audio-prepare"},
                "transcribe": {
                    "runtime": "faster-whisper",
                    "capabilities": {"supports_word_timestamps": False},
                },
            }
        )

        tasks = await build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"timestamps_granularity": "segment"},
            registry=registry,
            catalog=mock_catalog,
        )

        stages = [t.stage for t in tasks]
        assert "align" not in stages  # Skipped due to segment granularity

    @pytest.mark.asyncio
    async def test_word_granularity_includes_alignment(
        self, job_id, audio_uri, mock_catalog
    ):
        """timestamps_granularity=word includes alignment when needed."""
        registry = create_mock_registry(
            {
                "prepare": {"runtime": "audio-prepare"},
                "transcribe": {
                    "runtime": "faster-whisper",
                    "capabilities": {"supports_word_timestamps": False},
                },
                "align": {"runtime": "phoneme-align"},
            }
        )

        tasks = await build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"timestamps_granularity": "word"},
            registry=registry,
            catalog=mock_catalog,
        )

        stages = [t.stage for t in tasks]
        assert "align" in stages


class TestDagPerChannelWithCapabilities:
    """Tests per_channel DAG building with capability-driven selection."""

    @pytest.mark.asyncio
    async def test_per_channel_with_native_timestamps_skips_alignment(
        self, job_id, audio_uri, mock_catalog
    ):
        """per_channel mode with native timestamps skips alignment for all channels."""
        registry = create_mock_registry(
            {
                "prepare": {"runtime": "audio-prepare"},
                "transcribe": {
                    "runtime": "parakeet",
                    "capabilities": {
                        "supports_word_timestamps": True,
                        "languages": ["en"],
                    },
                },
            }
        )

        tasks = await build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "per_channel", "language": "en"},
            registry=registry,
            catalog=mock_catalog,
        )

        stages = [t.stage for t in tasks]
        assert "transcribe_ch0" in stages
        assert "transcribe_ch1" in stages
        assert "align_ch0" not in stages  # Skipped - native timestamps
        assert "align_ch1" not in stages
        assert "merge" not in stages
        assert len(tasks) == 3  # prepare, transcribe_ch0, transcribe_ch1

    @pytest.mark.asyncio
    async def test_per_channel_without_native_timestamps_includes_alignment(
        self, job_id, audio_uri, mock_catalog
    ):
        """per_channel mode without native timestamps includes alignment for all channels."""
        registry = create_mock_registry(
            {
                "prepare": {"runtime": "audio-prepare"},
                "transcribe": {
                    "runtime": "faster-whisper",
                    "capabilities": {"supports_word_timestamps": False},
                },
                "align": {"runtime": "phoneme-align"},
            }
        )

        tasks = await build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "per_channel"},
            registry=registry,
            catalog=mock_catalog,
        )

        stages = [t.stage for t in tasks]
        assert "transcribe_ch0" in stages
        assert "transcribe_ch1" in stages
        assert "align_ch0" in stages
        assert "align_ch1" in stages
        assert "merge" not in stages
        assert len(tasks) == 5  # prepare, 2x transcribe, 2x align


class TestEngineRanking:
    """Tests that the selector correctly ranks multiple capable engines."""

    @pytest.mark.asyncio
    async def test_prefers_native_timestamps_over_no_timestamps(
        self, job_id, audio_uri, mock_catalog
    ):
        """When multiple engines available, prefer one with native word timestamps."""
        registry = AsyncMock()

        # Both engines support the language
        slow_engine = make_engine_state(
            "faster-whisper",
            "transcribe",
            make_capabilities(
                "faster-whisper",
                stage="transcribe",
                supports_word_timestamps=False,
            ),
        )
        fast_engine = make_engine_state(
            "parakeet",
            "transcribe",
            make_capabilities(
                "parakeet",
                stage="transcribe",
                supports_word_timestamps=True,
            ),
        )

        async def get_by_stage(stage: str):
            if stage == "transcribe":
                return [slow_engine, fast_engine]
            if stage == "prepare":
                caps = make_capabilities("audio-prepare", stage="prepare")
                return [make_engine_state("audio-prepare", "prepare", caps)]
            return []

        registry.get_by_stage.side_effect = get_by_stage

        tasks = await build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={},
            registry=registry,
            catalog=mock_catalog,
        )

        by_stage = {t.stage: t for t in tasks}
        assert by_stage["transcribe"].runtime == "parakeet"
        assert "align" not in [t.stage for t in tasks]  # Skipped

    @pytest.mark.asyncio
    async def test_prefers_language_specific_over_universal(
        self, job_id, audio_uri, mock_catalog
    ):
        """When multiple engines available, prefer language-specific over universal."""
        registry = AsyncMock()

        universal = make_engine_state(
            "faster-whisper",
            "transcribe",
            make_capabilities(
                "faster-whisper",
                stage="transcribe",
                languages=None,  # Universal
            ),
        )
        english_specific = make_engine_state(
            "parakeet",
            "transcribe",
            make_capabilities(
                "parakeet",
                stage="transcribe",
                languages=["en"],  # English only
            ),
        )

        async def get_by_stage(stage: str):
            if stage == "transcribe":
                return [universal, english_specific]
            if stage == "prepare":
                caps = make_capabilities("audio-prepare", stage="prepare")
                return [make_engine_state("audio-prepare", "prepare", caps)]
            if stage == "align":
                caps = make_capabilities("phoneme-align", stage="align")
                return [make_engine_state("phoneme-align", "align", caps)]
            return []

        registry.get_by_stage.side_effect = get_by_stage

        tasks = await build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"language": "en"},
            registry=registry,
            catalog=mock_catalog,
        )

        by_stage = {t.stage: t for t in tasks}
        assert by_stage["transcribe"].runtime == "parakeet"


class TestDagDependencies:
    """Tests that DAG dependencies are correctly wired with capability-driven selection."""

    @pytest.mark.asyncio
    async def test_align_depends_on_transcribe(self, job_id, audio_uri, mock_catalog):
        """Align stage depends on transcribe."""
        registry = create_mock_registry(
            {
                "prepare": {"runtime": "audio-prepare"},
                "transcribe": {
                    "runtime": "faster-whisper",
                    "capabilities": {"supports_word_timestamps": False},
                },
                "align": {"runtime": "phoneme-align"},
            }
        )

        tasks = await build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={},
            registry=registry,
            catalog=mock_catalog,
        )

        by_stage = {t.stage: t for t in tasks}
        assert by_stage["align"].dependencies == [by_stage["transcribe"].id]

    @pytest.mark.asyncio
    async def test_diarize_depends_on_align(self, job_id, audio_uri, mock_catalog):
        """Diarize runs sequentially after transcribe/align."""
        registry = create_mock_registry(
            {
                "prepare": {"runtime": "audio-prepare"},
                "transcribe": {
                    "runtime": "faster-whisper",
                    "capabilities": {"includes_diarization": False},
                },
                "align": {"runtime": "phoneme-align"},
                "diarize": {"runtime": "pyannote-4.0"},
            }
        )

        tasks = await build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "diarize"},
            registry=registry,
            catalog=mock_catalog,
        )

        by_stage = {t.stage: t for t in tasks}
        diarize_deps = set(by_stage["diarize"].dependencies)

        # Diarize depends on prepare (audio) and align (last transcription stage)
        assert by_stage["prepare"].id in diarize_deps
        assert by_stage["align"].id in diarize_deps
        assert by_stage["transcribe"].dependencies == [by_stage["prepare"].id]


# =============================================================================
# Real Audio File Scenario Tests
# =============================================================================


class TestMergedWavScenarios:
    """Tests with test_merged.wav - various language and parameter combinations."""

    @pytest.fixture
    def audio_uri(self):
        return "s3://test-bucket/audio/test_merged.wav"

    @pytest.fixture
    def mock_catalog(self):
        catalog = MagicMock(spec=EngineCatalog)
        catalog.find_engines.return_value = []
        return catalog

    @pytest.mark.asyncio
    async def test_auto_language_selects_universal_engine(self, mock_catalog):
        """Auto language detection uses universal engine (faster-whisper)."""
        job_id = uuid4()
        registry = create_mock_registry(
            {
                "prepare": {"runtime": "audio-prepare"},
                "transcribe": {
                    "runtime": "faster-whisper",
                    "capabilities": {"languages": None},  # Universal
                },
                "align": {"runtime": "phoneme-align"},
            }
        )

        # No language specified = auto detection
        tasks = await build_task_dag(
            job_id=job_id,
            audio_uri="s3://test-bucket/audio/test_merged.wav",
            parameters={},
            registry=registry,
            catalog=mock_catalog,
        )

        by_stage = {t.stage: t for t in tasks}
        assert by_stage["transcribe"].runtime == "faster-whisper"
        assert "align" in [t.stage for t in tasks]  # Alignment included

    @pytest.mark.asyncio
    async def test_english_explicit_prefers_english_engine(self, mock_catalog):
        """Explicit English language prefers language-specific engine."""
        job_id = uuid4()
        registry = AsyncMock()

        # Two transcribe engines available
        universal = make_engine_state(
            "faster-whisper",
            "transcribe",
            make_capabilities("faster-whisper", "transcribe", languages=None),
        )
        english_only = make_engine_state(
            "parakeet",
            "transcribe",
            make_capabilities(
                "parakeet",
                "transcribe",
                languages=["en"],
                supports_word_timestamps=True,
            ),
        )

        async def get_by_stage(stage: str):
            if stage == "transcribe":
                return [universal, english_only]
            if stage == "prepare":
                return [
                    make_engine_state(
                        "audio-prepare",
                        "prepare",
                        make_capabilities("audio-prepare", "prepare"),
                    )
                ]
            if stage == "merge":
                return [
                    make_engine_state(
                        "final-merger",
                        "merge",
                        make_capabilities("final-merger", "merge"),
                    )
                ]
            return []

        registry.get_by_stage.side_effect = get_by_stage

        tasks = await build_task_dag(
            job_id=job_id,
            audio_uri="s3://test-bucket/audio/test_merged.wav",
            parameters={"language": "en"},
            registry=registry,
            catalog=mock_catalog,
        )

        by_stage = {t.stage: t for t in tasks}
        # Should prefer parakeet (language-specific + native timestamps)
        assert by_stage["transcribe"].runtime == "parakeet"
        # Parakeet has native timestamps, so no alignment needed
        assert "align" not in [t.stage for t in tasks]

    @pytest.mark.asyncio
    async def test_with_diarization_adds_diarize_stage(self, mock_catalog):
        """Diarization requested adds diarize stage when transcriber lacks native support."""
        job_id = uuid4()
        registry = create_mock_registry(
            {
                "prepare": {"runtime": "audio-prepare"},
                "transcribe": {
                    "runtime": "faster-whisper",
                    "capabilities": {
                        "languages": None,
                        "supports_word_timestamps": False,
                        "includes_diarization": False,
                    },
                },
                "align": {"runtime": "phoneme-align"},
                "diarize": {"runtime": "pyannote-4.0"},
            }
        )

        tasks = await build_task_dag(
            job_id=job_id,
            audio_uri="s3://test-bucket/audio/test_merged.wav",
            parameters={"speaker_detection": "diarize"},
            registry=registry,
            catalog=mock_catalog,
        )

        stages = [t.stage for t in tasks]
        assert "diarize" in stages
        assert "align" in stages
        assert "merge" not in stages
        assert len(tasks) == 4  # prepare, transcribe, align, diarize

    @pytest.mark.asyncio
    async def test_without_diarization_no_diarize_stage(self, mock_catalog):
        """No diarization requested = no diarize stage."""
        job_id = uuid4()
        registry = create_mock_registry(
            {
                "prepare": {"runtime": "audio-prepare"},
                "transcribe": {
                    "runtime": "faster-whisper",
                    "capabilities": {"languages": None},
                },
                "align": {"runtime": "phoneme-align"},
            }
        )

        tasks = await build_task_dag(
            job_id=job_id,
            audio_uri="s3://test-bucket/audio/test_merged.wav",
            parameters={},  # No speaker_detection
            registry=registry,
            catalog=mock_catalog,
        )

        stages = [t.stage for t in tasks]
        assert "diarize" not in stages
        assert "merge" not in stages
        assert len(tasks) == 3  # prepare, transcribe, align

    @pytest.mark.asyncio
    async def test_segment_timestamps_skips_alignment(self, mock_catalog):
        """Segment-level timestamps skip alignment stage."""
        job_id = uuid4()
        registry = create_mock_registry(
            {
                "prepare": {"runtime": "audio-prepare"},
                "transcribe": {
                    "runtime": "faster-whisper",
                    "capabilities": {
                        "languages": None,
                        "supports_word_timestamps": False,
                    },
                },
            }
        )

        tasks = await build_task_dag(
            job_id=job_id,
            audio_uri="s3://test-bucket/audio/test_merged.wav",
            parameters={"timestamps_granularity": "segment"},
            registry=registry,
            catalog=mock_catalog,
        )

        stages = [t.stage for t in tasks]
        assert "align" not in stages


class TestStageModelSelection:
    """Integration tests for M55 stage model selection contract."""

    @pytest.mark.asyncio
    async def test_stage_runtime_model_ids_are_injected(
        self, job_id, audio_uri, mock_catalog
    ):
        registry = create_mock_registry(
            {
                "prepare": {"runtime": "audio-prepare"},
                "transcribe": {
                    "runtime": "faster-whisper",
                    "capabilities": {
                        "supports_word_timestamps": False,
                        "includes_diarization": False,
                    },
                },
                "align": {"runtime": "phoneme-align"},
                "diarize": {"runtime": "pyannote-4.0"},
                "pii_detect": {"runtime": "pii-presidio"},
                "merge": {"runtime": "final-merger"},
            }
        )
        runtime_index = {
            "faster-whisper": make_engine_state(
                "faster-whisper",
                "transcribe",
                make_capabilities(
                    runtime="faster-whisper",
                    stage="transcribe",
                    supports_word_timestamps=False,
                    includes_diarization=False,
                ),
            ),
            "phoneme-align": make_engine_state("phoneme-align", "align"),
            "pyannote-4.0": make_engine_state("pyannote-4.0", "diarize"),
            "pii-presidio": make_engine_state("pii-presidio", "pii_detect"),
        }
        registry.get_engine.side_effect = lambda runtime: runtime_index.get(runtime)

        mock_db = AsyncMock()
        mock_db.execute.side_effect = [
            _ScalarOneResult(None),  # model_transcribe preference -> not a model
            _ScalarOneResult(
                SimpleNamespace(
                    id="facebook/wav2vec2-base-960h",
                    stage="align",
                    status="ready",
                    runtime="phoneme-align",
                    runtime_model_id="facebook/wav2vec2-base-960h",
                    source="facebook/wav2vec2-base-960h",
                    languages=["en"],
                )
            ),
            _ScalarOneResult(
                SimpleNamespace(
                    id="pyannote/speaker-diarization-community-1",
                    stage="diarize",
                    status="ready",
                    runtime="pyannote-4.0",
                    runtime_model_id="pyannote/speaker-diarization-community-1",
                    source="pyannote/speaker-diarization-community-1",
                    languages=None,
                )
            ),
            _ScalarOneResult(
                SimpleNamespace(
                    id="urchade/gliner_multi-v2.1",
                    stage="pii_detect",
                    status="ready",
                    runtime="pii-presidio",
                    runtime_model_id="urchade/gliner_multi-v2.1",
                    source="urchade/gliner_multi-v2.1",
                    languages=None,
                )
            ),
        ]

        tasks = await build_task_dag(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={
                "model_transcribe": "faster-whisper",
                "timestamps_granularity": "word",
                "speaker_detection": "diarize",
                "pii_detection": True,
                "model_align": "facebook/wav2vec2-base-960h",
                "model_diarize": "pyannote/speaker-diarization-community-1",
                "model_pii_detect": "urchade/gliner_multi-v2.1",
            },
            registry=registry,
            catalog=mock_catalog,
            db=mock_db,
        )

        by_stage = {t.stage: t for t in tasks}
        assert (
            by_stage["align"].config["runtime_model_id"]
            == "facebook/wav2vec2-base-960h"
        )
        assert (
            by_stage["diarize"].config["runtime_model_id"]
            == "pyannote/speaker-diarization-community-1"
        )
        # pii_detect is post-processing; not a DAG task
        assert "pii_detect" not in by_stage

    @pytest.mark.asyncio
    async def test_stage_mismatch_rejected(self, job_id, audio_uri, mock_catalog):
        registry = create_mock_registry(
            {
                "prepare": {"runtime": "audio-prepare"},
                "transcribe": {
                    "runtime": "faster-whisper",
                    "capabilities": {
                        "supports_word_timestamps": False,
                        "includes_diarization": False,
                    },
                },
                "align": {"runtime": "phoneme-align"},
                "merge": {"runtime": "final-merger"},
            }
        )
        runtime_index = {
            "faster-whisper": make_engine_state(
                "faster-whisper",
                "transcribe",
                make_capabilities(
                    runtime="faster-whisper",
                    stage="transcribe",
                    supports_word_timestamps=False,
                    includes_diarization=False,
                ),
            ),
            "phoneme-align": make_engine_state("phoneme-align", "align"),
        }
        registry.get_engine.side_effect = lambda runtime: runtime_index.get(runtime)

        mock_db = AsyncMock()
        mock_db.execute.side_effect = [
            _ScalarOneResult(None),  # model_transcribe preference -> not a model
            _ScalarOneResult(
                SimpleNamespace(
                    id="urchade/gliner_multi-v2.1",
                    stage="pii_detect",  # wrong stage for align
                    status="ready",
                    runtime="pii-presidio",
                    runtime_model_id="urchade/gliner_multi-v2.1",
                    source="urchade/gliner_multi-v2.1",
                    languages=None,
                )
            ),
        ]

        with pytest.raises(ModelSelectionError) as exc_info:
            await build_task_dag(
                job_id=job_id,
                audio_uri=audio_uri,
                parameters={
                    "model_transcribe": "faster-whisper",
                    "timestamps_granularity": "word",
                    "model_align": "urchade/gliner_multi-v2.1",
                },
                registry=registry,
                catalog=mock_catalog,
                db=mock_db,
            )

        assert exc_info.value.code == "model_stage_mismatch"


class TestPiiCombinedWavScenarios:
    """Tests with test_pii_combined.wav - Croatian language."""

    @pytest.fixture
    def mock_catalog(self):
        catalog = MagicMock(spec=EngineCatalog)
        catalog.find_engines.return_value = []
        return catalog

    @pytest.mark.asyncio
    async def test_croatian_requires_universal_engine(self, mock_catalog):
        """Croatian language requires universal engine (faster-whisper)."""
        job_id = uuid4()
        registry = create_mock_registry(
            {
                "prepare": {"runtime": "audio-prepare"},
                "transcribe": {
                    "runtime": "faster-whisper",
                    "capabilities": {"languages": None},  # Supports all languages
                },
                "align": {"runtime": "phoneme-align"},
                "merge": {"runtime": "final-merger"},
            }
        )

        tasks = await build_task_dag(
            job_id=job_id,
            audio_uri="s3://test-bucket/audio/test_pii_combined.wav",
            parameters={"language": "hr"},  # Croatian
            registry=registry,
            catalog=mock_catalog,
        )

        by_stage = {t.stage: t for t in tasks}
        assert by_stage["transcribe"].runtime == "faster-whisper"

    @pytest.mark.asyncio
    async def test_croatian_rejects_english_only_engine(self, mock_catalog):
        """Croatian language rejects English-only engine."""
        job_id = uuid4()
        registry = create_mock_registry(
            {
                "prepare": {"runtime": "audio-prepare"},
                "transcribe": {
                    "runtime": "parakeet",
                    "capabilities": {"languages": ["en"]},  # English only
                },
                "merge": {"runtime": "final-merger"},
            }
        )

        with pytest.raises(NoCapableEngineError) as exc_info:
            await build_task_dag(
                job_id=job_id,
                audio_uri="s3://test-bucket/audio/test_pii_combined.wav",
                parameters={"language": "hr"},
                registry=registry,
                catalog=mock_catalog,
            )

        assert exc_info.value.stage == "transcribe"
        assert "hr" in str(exc_info.value)


class TestStereoSpeakersWavScenarios:
    """Tests with test_stereo_speakers.wav - per-channel diarization."""

    @pytest.fixture
    def mock_catalog(self):
        catalog = MagicMock(spec=EngineCatalog)
        catalog.find_engines.return_value = []
        return catalog

    @pytest.mark.asyncio
    async def test_per_channel_creates_channel_tasks(self, mock_catalog):
        """Per-channel mode creates separate tasks for each channel (no merge)."""
        job_id = uuid4()
        registry = create_mock_registry(
            {
                "prepare": {"runtime": "audio-prepare"},
                "transcribe": {
                    "runtime": "faster-whisper",
                    "capabilities": {
                        "languages": None,
                        "supports_word_timestamps": False,
                    },
                },
                "align": {"runtime": "phoneme-align"},
            }
        )

        tasks = await build_task_dag(
            job_id=job_id,
            audio_uri="s3://test-bucket/audio/test_stereo_speakers.wav",
            parameters={"speaker_detection": "per_channel"},
            registry=registry,
            catalog=mock_catalog,
        )

        stages = [t.stage for t in tasks]
        assert "transcribe_ch0" in stages
        assert "transcribe_ch1" in stages
        assert "align_ch0" in stages
        assert "align_ch1" in stages
        assert "merge" not in stages
        # No single "transcribe" or "align" stage
        assert "transcribe" not in stages
        assert "align" not in stages

    @pytest.mark.asyncio
    async def test_per_channel_with_native_timestamps(self, mock_catalog):
        """Per-channel with native timestamps skips alignment for all channels."""
        job_id = uuid4()
        registry = create_mock_registry(
            {
                "prepare": {"runtime": "audio-prepare"},
                "transcribe": {
                    "runtime": "parakeet",
                    "capabilities": {
                        "languages": ["en"],
                        "supports_word_timestamps": True,
                    },
                },
            }
        )

        tasks = await build_task_dag(
            job_id=job_id,
            audio_uri="s3://test-bucket/audio/test_stereo_speakers.wav",
            parameters={"speaker_detection": "per_channel", "language": "en"},
            registry=registry,
            catalog=mock_catalog,
        )

        stages = [t.stage for t in tasks]
        assert "transcribe_ch0" in stages
        assert "transcribe_ch1" in stages
        assert "align_ch0" not in stages
        assert "align_ch1" not in stages
        assert "merge" not in stages
        assert len(tasks) == 3  # prepare, transcribe_ch0, transcribe_ch1

    @pytest.mark.asyncio
    async def test_per_channel_all_transcribes_depend_on_prepare(self, mock_catalog):
        """All per-channel transcribe tasks depend on prepare."""
        job_id = uuid4()
        registry = create_mock_registry(
            {
                "prepare": {"runtime": "audio-prepare"},
                "transcribe": {
                    "runtime": "faster-whisper",
                    "capabilities": {
                        "languages": None,
                        "supports_word_timestamps": False,
                    },
                },
                "align": {"runtime": "phoneme-align"},
            }
        )

        tasks = await build_task_dag(
            job_id=job_id,
            audio_uri="s3://test-bucket/audio/test_stereo_speakers.wav",
            parameters={"speaker_detection": "per_channel"},
            registry=registry,
            catalog=mock_catalog,
        )

        by_stage = {t.stage: t for t in tasks}
        prepare_id = by_stage["prepare"].id

        assert by_stage["transcribe_ch0"].dependencies == [prepare_id]
        assert by_stage["transcribe_ch1"].dependencies == [prepare_id]
