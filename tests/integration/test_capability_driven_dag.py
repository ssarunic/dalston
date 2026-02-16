"""Integration tests for M31 capability-driven DAG building.

Tests that build_task_dag_async correctly adapts DAG shape based on
engine capabilities (native word timestamps, native diarization).
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from dalston.engine_sdk.types import EngineCapabilities
from dalston.orchestrator.catalog import CatalogEntry, EngineCatalog
from dalston.orchestrator.dag import build_task_dag_async
from dalston.orchestrator.engine_selector import NoCapableEngineError
from dalston.orchestrator.registry import BatchEngineState

# =============================================================================
# Test Fixtures
# =============================================================================


def make_capabilities(
    engine_id: str,
    stage: str = "transcribe",
    languages: list[str] | None = None,
    supports_word_timestamps: bool = False,
    includes_diarization: bool = False,
    supports_streaming: bool = False,
    rtf_gpu: float | None = None,
) -> EngineCapabilities:
    """Create EngineCapabilities for testing."""
    return EngineCapabilities(
        engine_id=engine_id,
        version="1.0.0",
        stages=[stage],
        languages=languages,
        supports_word_timestamps=supports_word_timestamps,
        includes_diarization=includes_diarization,
        supports_streaming=supports_streaming,
        rtf_gpu=rtf_gpu,
    )


def make_engine_state(
    engine_id: str,
    stage: str,
    capabilities: EngineCapabilities | None = None,
    is_available: bool = True,
) -> BatchEngineState:
    """Create BatchEngineState for testing."""
    now = datetime.now(UTC)
    return BatchEngineState(
        engine_id=engine_id,
        stage=stage,
        queue_name=f"dalston:queue:{engine_id}",
        status="idle" if is_available else "offline",
        current_task=None,
        last_heartbeat=now,
        registered_at=now,
        capabilities=capabilities,
    )


def make_catalog_entry(
    engine_id: str,
    stage: str = "transcribe",
    languages: list[str] | None = None,
) -> CatalogEntry:
    """Create CatalogEntry for testing."""
    return CatalogEntry(
        engine_id=engine_id,
        image=f"dalston/{engine_id}:latest",
        capabilities=make_capabilities(
            engine_id=engine_id, stage=stage, languages=languages
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
            - engine_id: str
            - capabilities: EngineCapabilities or dict with capability options

    Returns:
        Mock BatchEngineRegistry
    """
    registry = AsyncMock()

    engines_by_stage: dict[str, list[BatchEngineState]] = {}

    for stage, config in engine_configs.items():
        engine_id = config["engine_id"]
        caps_config = config.get("capabilities", {})

        if isinstance(caps_config, EngineCapabilities):
            caps = caps_config
        else:
            caps = make_capabilities(
                engine_id=engine_id,
                stage=stage,
                **caps_config,
            )

        engine = make_engine_state(engine_id, stage, caps)
        engines_by_stage.setdefault(stage, []).append(engine)

    async def get_engines_for_stage(stage: str):
        return engines_by_stage.get(stage, [])

    registry.get_engines_for_stage.side_effect = get_engines_for_stage

    return registry


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
                "prepare": {"engine_id": "audio-prepare"},
                "transcribe": {
                    "engine_id": "parakeet",
                    "capabilities": {
                        "supports_word_timestamps": True,
                        "languages": ["en"],
                    },
                },
                "merge": {"engine_id": "final-merger"},
            }
        )

        tasks = await build_task_dag_async(
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
        assert "merge" in stages
        assert len(tasks) == 3  # prepare, transcribe, merge (no align)

    @pytest.mark.asyncio
    async def test_includes_alignment_without_native_timestamps(
        self, job_id, audio_uri, mock_catalog
    ):
        """Transcriber without native timestamps -> align stage included."""
        registry = create_mock_registry(
            {
                "prepare": {"engine_id": "audio-prepare"},
                "transcribe": {
                    "engine_id": "faster-whisper",
                    "capabilities": {"supports_word_timestamps": False},
                },
                "align": {"engine_id": "whisperx-align"},
                "merge": {"engine_id": "final-merger"},
            }
        )

        tasks = await build_task_dag_async(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={},
            registry=registry,
            catalog=mock_catalog,
        )

        stages = [t.stage for t in tasks]
        assert "align" in stages  # Included because transcriber lacks native support
        assert len(tasks) == 4  # prepare, transcribe, align, merge


class TestDagShapeWithNativeDiarization:
    """Tests that DAG skips diarization when transcriber has native diarization."""

    @pytest.mark.asyncio
    async def test_skips_diarization_with_native_support(
        self, job_id, audio_uri, mock_catalog
    ):
        """Transcriber with native diarization -> no diarize stage even if requested."""
        registry = create_mock_registry(
            {
                "prepare": {"engine_id": "audio-prepare"},
                "transcribe": {
                    "engine_id": "whisperx-full",
                    "capabilities": {
                        "supports_word_timestamps": True,
                        "includes_diarization": True,
                    },
                },
                "merge": {"engine_id": "final-merger"},
            }
        )

        tasks = await build_task_dag_async(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "diarize"},
            registry=registry,
            catalog=mock_catalog,
        )

        stages = [t.stage for t in tasks]
        assert "diarize" not in stages  # Skipped - transcriber has native diarization
        assert "align" not in stages  # Skipped - transcriber has native timestamps
        assert len(tasks) == 3  # prepare, transcribe, merge

    @pytest.mark.asyncio
    async def test_includes_diarization_without_native_support(
        self, job_id, audio_uri, mock_catalog
    ):
        """Transcriber without native diarization -> diarize stage included when requested."""
        registry = create_mock_registry(
            {
                "prepare": {"engine_id": "audio-prepare"},
                "transcribe": {
                    "engine_id": "faster-whisper",
                    "capabilities": {
                        "supports_word_timestamps": False,
                        "includes_diarization": False,
                    },
                },
                "align": {"engine_id": "whisperx-align"},
                "diarize": {"engine_id": "pyannote-3.1"},
                "merge": {"engine_id": "final-merger"},
            }
        )

        tasks = await build_task_dag_async(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "diarize"},
            registry=registry,
            catalog=mock_catalog,
        )

        stages = [t.stage for t in tasks]
        assert "diarize" in stages  # Included when requested
        assert "align" in stages
        assert len(tasks) == 5  # prepare, diarize, transcribe, align, merge


class TestDagShapeWithLanguageRequirements:
    """Tests DAG building with language requirements."""

    @pytest.mark.asyncio
    async def test_selects_language_capable_engine(
        self, job_id, audio_uri, mock_catalog
    ):
        """Engine supporting requested language is selected."""
        registry = create_mock_registry(
            {
                "prepare": {"engine_id": "audio-prepare"},
                "transcribe": {
                    "engine_id": "faster-whisper",
                    "capabilities": {"languages": None},  # Universal
                },
                "align": {"engine_id": "whisperx-align"},
                "merge": {"engine_id": "final-merger"},
            }
        )

        tasks = await build_task_dag_async(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"language": "hr"},  # Croatian
            registry=registry,
            catalog=mock_catalog,
        )

        by_stage = {t.stage: t for t in tasks}
        assert by_stage["transcribe"].engine_id == "faster-whisper"

    @pytest.mark.asyncio
    async def test_raises_error_when_no_engine_supports_language(
        self, job_id, audio_uri, mock_catalog
    ):
        """NoCapableEngineError raised when no engine supports the language."""
        registry = create_mock_registry(
            {
                "prepare": {"engine_id": "audio-prepare"},
                "transcribe": {
                    "engine_id": "parakeet",
                    "capabilities": {"languages": ["en"]},  # English only
                },
                "merge": {"engine_id": "final-merger"},
            }
        )

        with pytest.raises(NoCapableEngineError) as exc_info:
            await build_task_dag_async(
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
                "prepare": {"engine_id": "audio-prepare"},
                "transcribe": {
                    "engine_id": "faster-whisper",
                    "capabilities": {"supports_word_timestamps": False},
                },
                "merge": {"engine_id": "final-merger"},
            }
        )

        tasks = await build_task_dag_async(
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
                "prepare": {"engine_id": "audio-prepare"},
                "transcribe": {
                    "engine_id": "faster-whisper",
                    "capabilities": {"supports_word_timestamps": False},
                },
                "align": {"engine_id": "whisperx-align"},
                "merge": {"engine_id": "final-merger"},
            }
        )

        tasks = await build_task_dag_async(
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
                "prepare": {"engine_id": "audio-prepare"},
                "transcribe": {
                    "engine_id": "parakeet",
                    "capabilities": {
                        "supports_word_timestamps": True,
                        "languages": ["en"],
                    },
                },
                "merge": {"engine_id": "final-merger"},
            }
        )

        tasks = await build_task_dag_async(
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
        assert len(tasks) == 4  # prepare, transcribe_ch0, transcribe_ch1, merge

    @pytest.mark.asyncio
    async def test_per_channel_without_native_timestamps_includes_alignment(
        self, job_id, audio_uri, mock_catalog
    ):
        """per_channel mode without native timestamps includes alignment for all channels."""
        registry = create_mock_registry(
            {
                "prepare": {"engine_id": "audio-prepare"},
                "transcribe": {
                    "engine_id": "faster-whisper",
                    "capabilities": {"supports_word_timestamps": False},
                },
                "align": {"engine_id": "whisperx-align"},
                "merge": {"engine_id": "final-merger"},
            }
        )

        tasks = await build_task_dag_async(
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
        assert len(tasks) == 6  # prepare, 2x transcribe, 2x align, merge


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

        async def get_engines_for_stage(stage: str):
            if stage == "transcribe":
                return [slow_engine, fast_engine]
            if stage == "prepare":
                caps = make_capabilities("audio-prepare", stage="prepare")
                return [make_engine_state("audio-prepare", "prepare", caps)]
            if stage == "merge":
                caps = make_capabilities("final-merger", stage="merge")
                return [make_engine_state("final-merger", "merge", caps)]
            return []

        registry.get_engines_for_stage.side_effect = get_engines_for_stage

        tasks = await build_task_dag_async(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={},
            registry=registry,
            catalog=mock_catalog,
        )

        by_stage = {t.stage: t for t in tasks}
        assert by_stage["transcribe"].engine_id == "parakeet"
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

        async def get_engines_for_stage(stage: str):
            if stage == "transcribe":
                return [universal, english_specific]
            if stage == "prepare":
                caps = make_capabilities("audio-prepare", stage="prepare")
                return [make_engine_state("audio-prepare", "prepare", caps)]
            if stage == "align":
                caps = make_capabilities("whisperx-align", stage="align")
                return [make_engine_state("whisperx-align", "align", caps)]
            if stage == "merge":
                caps = make_capabilities("final-merger", stage="merge")
                return [make_engine_state("final-merger", "merge", caps)]
            return []

        registry.get_engines_for_stage.side_effect = get_engines_for_stage

        tasks = await build_task_dag_async(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"language": "en"},
            registry=registry,
            catalog=mock_catalog,
        )

        by_stage = {t.stage: t for t in tasks}
        assert by_stage["transcribe"].engine_id == "parakeet"


class TestDagDependencies:
    """Tests that DAG dependencies are correctly wired with capability-driven selection."""

    @pytest.mark.asyncio
    async def test_merge_depends_on_correct_stages(
        self, job_id, audio_uri, mock_catalog
    ):
        """Merge depends on all prior stages (prepare, transcribe, optional align)."""
        registry = create_mock_registry(
            {
                "prepare": {"engine_id": "audio-prepare"},
                "transcribe": {
                    "engine_id": "faster-whisper",
                    "capabilities": {"supports_word_timestamps": False},
                },
                "align": {"engine_id": "whisperx-align"},
                "merge": {"engine_id": "final-merger"},
            }
        )

        tasks = await build_task_dag_async(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={},
            registry=registry,
            catalog=mock_catalog,
        )

        by_stage = {t.stage: t for t in tasks}
        merge_deps = set(by_stage["merge"].dependencies)

        assert by_stage["prepare"].id in merge_deps
        assert by_stage["transcribe"].id in merge_deps
        assert by_stage["align"].id in merge_deps

    @pytest.mark.asyncio
    async def test_align_depends_on_transcribe(self, job_id, audio_uri, mock_catalog):
        """Align stage depends on transcribe."""
        registry = create_mock_registry(
            {
                "prepare": {"engine_id": "audio-prepare"},
                "transcribe": {
                    "engine_id": "faster-whisper",
                    "capabilities": {"supports_word_timestamps": False},
                },
                "align": {"engine_id": "whisperx-align"},
                "merge": {"engine_id": "final-merger"},
            }
        )

        tasks = await build_task_dag_async(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={},
            registry=registry,
            catalog=mock_catalog,
        )

        by_stage = {t.stage: t for t in tasks}
        assert by_stage["align"].dependencies == [by_stage["transcribe"].id]

    @pytest.mark.asyncio
    async def test_diarize_depends_only_on_prepare(
        self, job_id, audio_uri, mock_catalog
    ):
        """Diarize runs parallel to transcribe, both depend on prepare."""
        registry = create_mock_registry(
            {
                "prepare": {"engine_id": "audio-prepare"},
                "transcribe": {
                    "engine_id": "faster-whisper",
                    "capabilities": {"includes_diarization": False},
                },
                "align": {"engine_id": "whisperx-align"},
                "diarize": {"engine_id": "pyannote-3.1"},
                "merge": {"engine_id": "final-merger"},
            }
        )

        tasks = await build_task_dag_async(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters={"speaker_detection": "diarize"},
            registry=registry,
            catalog=mock_catalog,
        )

        by_stage = {t.stage: t for t in tasks}
        prepare_id = by_stage["prepare"].id

        assert by_stage["diarize"].dependencies == [prepare_id]
        assert by_stage["transcribe"].dependencies == [prepare_id]
