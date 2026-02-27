"""Task DAG builder for job expansion.

Converts job parameters into a directed acyclic graph of tasks.
Each task represents a processing step executed by a specific engine.

M31: Added capability-driven engine selection via build_task_dag_async().
The original build_task_dag() is preserved as a fallback.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import structlog

from dalston.common.models import Task, TaskStatus

if TYPE_CHECKING:
    from dalston.orchestrator.catalog import EngineCatalog
    from dalston.orchestrator.registry import BatchEngineRegistry

logger = structlog.get_logger()

# Valid values for timestamps_granularity API parameter
VALID_TIMESTAMPS_GRANULARITIES = {"word", "segment", "none"}

# Valid values for speaker_detection API parameter
VALID_SPEAKER_DETECTION_MODES = {"none", "diarize", "per_channel"}


# Default engine IDs for each stage
DEFAULT_ENGINES = {
    "prepare": "audio-prepare",
    "transcribe": "faster-whisper-base",
    "align": "whisperx-align",
    "diarize": "pyannote-3.1",
    "pii_detect": "pii-presidio",
    "audio_redact": "audio-redactor",
    "merge": "final-merger",
}

# Valid PII redaction modes
VALID_PII_REDACTION_MODES = {"silence", "beep"}

# Default transcription config
DEFAULT_TRANSCRIBE_CONFIG = {
    "model": "large-v3",
    "language": None,  # Auto-detect
    "beam_size": 5,
    "vad_filter": True,
}

# Engines that produce native word timestamps (skip ALIGN stage)
# DEPRECATED: Use capability-driven routing (supports_word_timestamps) instead
NATIVE_WORD_TIMESTAMP_ENGINES = {"parakeet"}


# =============================================================================
# M36: Model Registry for Runtime Model Management
# =============================================================================
# Maps public Dalston model IDs to runtime + runtime_model_id.
# The orchestrator uses this to:
# 1. Determine which runtime can serve a model request
# 2. Set config["runtime_model_id"] in the task payload
# 3. Route to any engine of the appropriate runtime
#
# Each entry specifies:
#   - runtime: The engine runtime (e.g., "faster-whisper", "nemo")
#   - runtime_model_id: The model ID passed to the underlying library
#   - supports_word_timestamps: Whether engine produces native word timestamps
#   - languages: Supported languages (None = all)

MODEL_REGISTRY: dict[str, dict] = {
    # Faster-Whisper models (CTranslate2-based Whisper)
    "faster-whisper-base": {
        "runtime": "faster-whisper",
        "runtime_model_id": "base",
        "supports_word_timestamps": False,
        "languages": None,  # 99 languages
    },
    "faster-whisper-large-v3": {
        "runtime": "faster-whisper",
        "runtime_model_id": "large-v3",
        "supports_word_timestamps": False,
        "languages": None,
    },
    "faster-whisper-large-v3-turbo": {
        "runtime": "faster-whisper",
        "runtime_model_id": "large-v3-turbo",
        "supports_word_timestamps": False,
        "languages": None,
    },
    # Parakeet models (NVIDIA NeMo-based)
    "parakeet-ctc-0.6b": {
        "runtime": "nemo",
        "runtime_model_id": "nvidia/parakeet-ctc-0.6b",
        "supports_word_timestamps": True,
        "languages": ["en"],
    },
    "parakeet-ctc-1.1b": {
        "runtime": "nemo",
        "runtime_model_id": "nvidia/parakeet-ctc-1.1b",
        "supports_word_timestamps": True,
        "languages": ["en"],
    },
    "parakeet-tdt-0.6b-v3": {
        "runtime": "nemo",
        "runtime_model_id": "nvidia/parakeet-tdt-0.6b-v3",
        "supports_word_timestamps": True,
        "languages": ["en"],
    },
    "parakeet-tdt-1.1b": {
        "runtime": "nemo",
        "runtime_model_id": "nvidia/parakeet-tdt-1.1b",
        "supports_word_timestamps": True,
        "languages": ["en"],
    },
}


def resolve_model(model_id: str) -> dict | None:
    """Resolve a public model ID to runtime information.

    Args:
        model_id: Public Dalston model ID (e.g., "faster-whisper-large-v3-turbo")

    Returns:
        Dict with runtime, runtime_model_id, supports_word_timestamps, languages
        or None if model is not found in registry
    """
    return MODEL_REGISTRY.get(model_id)


def build_task_dag(job_id: UUID, audio_uri: str, parameters: dict) -> list[Task]:
    """Build a task DAG for a job.

    M04 implementation: Creates a pipeline with optional speaker detection:

    Mode: none (default)
        prepare → transcribe → [align] → merge

    Mode: diarize
        prepare → transcribe → [align] → merge
                ↘ diarize ─────────────↗

    Mode: per_channel (stereo audio)
        prepare ─┬→ transcribe_ch0 → [align_ch0] ─┬→ merge
                 └→ transcribe_ch1 → [align_ch1] ─┘

    Args:
        job_id: The job's UUID
        audio_uri: S3 URI to the audio file
        parameters: Job parameters including:
            - model: Whisper model size (default: large-v3)
            - language: Language code or None for auto-detect
            - beam_size: Beam search width (default: 5)
            - vad_filter: Enable VAD filtering (default: True)
            - word_timestamps: Enable word-level alignment (default: True)
            - speaker_detection: "none", "diarize", or "per_channel" (default: none)
            - num_speakers: Exact speaker count hint (optional, for diarize)
            - min_speakers: Minimum speaker count hint (optional, for diarize)
            - max_speakers: Maximum speaker count hint (optional, for diarize)

    Returns:
        List of Task objects with dependencies wired
    """
    # Extract engine overrides from parameters (for testing/flexibility)
    engines = {
        "prepare": parameters.get("engine_prepare", DEFAULT_ENGINES["prepare"]),
        "transcribe": parameters.get(
            "engine_transcribe", DEFAULT_ENGINES["transcribe"]
        ),
        "align": parameters.get("engine_align", DEFAULT_ENGINES["align"]),
        "diarize": parameters.get("engine_diarize", DEFAULT_ENGINES["diarize"]),
        "pii_detect": parameters.get(
            "engine_pii_detect", DEFAULT_ENGINES["pii_detect"]
        ),
        "audio_redact": parameters.get(
            "engine_audio_redact", DEFAULT_ENGINES["audio_redact"]
        ),
        "merge": parameters.get("engine_merge", DEFAULT_ENGINES["merge"]),
    }

    # Check if word timestamps (alignment) is enabled
    # Supports both API style (timestamps_granularity) and direct (word_timestamps)
    if "word_timestamps" in parameters:
        word_timestamps = parameters["word_timestamps"]
    elif "timestamps_granularity" in parameters:
        granularity = parameters["timestamps_granularity"]
        if granularity not in VALID_TIMESTAMPS_GRANULARITIES:
            logger.warning(
                f"Unknown timestamps_granularity '{granularity}', "
                f"expected one of {VALID_TIMESTAMPS_GRANULARITIES}. Defaulting to 'word'."
            )
            granularity = "word"
        # API style: "word" enables alignment, "segment"/"none" disables it
        word_timestamps = granularity == "word"
    else:
        word_timestamps = True  # Default: enable word-level timestamps

    # Check speaker detection mode
    speaker_detection = parameters.get("speaker_detection", "none")
    if speaker_detection not in VALID_SPEAKER_DETECTION_MODES:
        logger.warning(
            f"Unknown speaker_detection '{speaker_detection}', "
            f"expected one of {VALID_SPEAKER_DETECTION_MODES}. Defaulting to 'none'."
        )
        speaker_detection = "none"

    # Build diarization config from speaker hints
    # Use `is not None` to allow num_speakers=0 edge case (auto-detect)
    diarize_config = {}
    if parameters.get("num_speakers") is not None and parameters["num_speakers"] > 0:
        # num_speakers sets both min and max to the same value
        diarize_config["min_speakers"] = parameters["num_speakers"]
        diarize_config["max_speakers"] = parameters["num_speakers"]
    else:
        if (
            parameters.get("min_speakers") is not None
            and parameters["min_speakers"] > 0
        ):
            diarize_config["min_speakers"] = parameters["min_speakers"]
        if (
            parameters.get("max_speakers") is not None
            and parameters["max_speakers"] > 0
        ):
            diarize_config["max_speakers"] = parameters["max_speakers"]

    # Exclusive mode for pyannote 4.0+ (one speaker per segment)
    if parameters.get("exclusive"):
        diarize_config["exclusive"] = True

    # Build transcription config from parameters
    # Gateway may pass pre-built transcribe_config (from model registry)
    if "transcribe_config" in parameters:
        transcribe_config = {
            **DEFAULT_TRANSCRIBE_CONFIG,
            **parameters["transcribe_config"],
        }
        # Override language from top-level if provided
        if parameters.get("language"):
            transcribe_config["language"] = parameters["language"]
    else:
        # Legacy: build from top-level parameters
        transcribe_config = {
            "model": parameters.get("model", DEFAULT_TRANSCRIBE_CONFIG["model"]),
            "language": parameters.get(
                "language", DEFAULT_TRANSCRIBE_CONFIG["language"]
            ),
            "beam_size": parameters.get(
                "beam_size", DEFAULT_TRANSCRIBE_CONFIG["beam_size"]
            ),
            "vad_filter": parameters.get(
                "vad_filter", DEFAULT_TRANSCRIBE_CONFIG["vad_filter"]
            ),
        }

    # M36: Resolve model ID to runtime information
    # If the engine_transcribe matches a model in MODEL_REGISTRY, use runtime routing
    model_info = resolve_model(engines["transcribe"])
    if model_info is not None:
        # Override engine_id with runtime (e.g., "faster-whisper" instead of "faster-whisper-large-v3-turbo")
        engines["transcribe"] = model_info["runtime"]
        # Add runtime_model_id to config so engine knows which model to load
        transcribe_config["runtime_model_id"] = model_info["runtime_model_id"]
        logger.debug(
            "model_resolved",
            original_engine=engines["transcribe"],
            runtime=model_info["runtime"],
            runtime_model_id=model_info["runtime_model_id"],
        )

    tasks = []
    diarize_task = None  # Track diarize task for merge dependencies

    # Prepare config - enable channel splitting for per_channel mode
    prepare_config = {}
    if speaker_detection == "per_channel":
        prepare_config["split_channels"] = True

    # Task 1: Audio preparation (no dependencies)
    # Converts uploaded audio to 16kHz mono WAV (or splits channels)
    prepare_task = Task(
        id=uuid4(),
        job_id=job_id,
        stage="prepare",
        engine_id=engines["prepare"],
        status=TaskStatus.PENDING,
        dependencies=[],
        config=prepare_config,
        input_uri=audio_uri,
        output_uri=None,
        retries=0,
        max_retries=2,
        required=True,
    )
    tasks.append(prepare_task)

    # Handle per_channel mode: parallel transcription per channel
    if speaker_detection == "per_channel":
        num_channels = parameters.get("num_channels", 2)
        return _build_per_channel_dag(
            tasks=tasks,
            prepare_task=prepare_task,
            job_id=job_id,
            engines=engines,
            transcribe_config=transcribe_config,
            word_timestamps=word_timestamps,
            num_channels=num_channels,
            parameters=parameters,
        )

    # Task (optional): Diarization (depends only on prepare, runs parallel with transcribe/align)
    # Identifies who speaks when
    if speaker_detection == "diarize":
        diarize_task = Task(
            id=uuid4(),
            job_id=job_id,
            stage="diarize",
            engine_id=engines["diarize"],
            status=TaskStatus.PENDING,
            dependencies=[prepare_task.id],
            config=diarize_config,
            input_uri=None,  # Will use audio from prepare
            output_uri=None,
            retries=0,
            max_retries=2,
            required=True,
        )
        tasks.append(diarize_task)

    # Task: Transcription (depends on prepare)
    # Uses prepared audio from prepare stage
    transcribe_task = Task(
        id=uuid4(),
        job_id=job_id,
        stage="transcribe",
        engine_id=engines["transcribe"],
        status=TaskStatus.PENDING,
        dependencies=[prepare_task.id],
        config=transcribe_config,
        input_uri=None,  # Will use audio_uri from prepare output
        output_uri=None,
        retries=0,
        max_retries=2,
        required=True,
    )
    tasks.append(transcribe_task)

    # Task (optional): Alignment (depends on transcribe)
    # Adds precise word-level timestamps using wav2vec2 forced alignment
    # Skip alignment for engines that produce native word timestamps (e.g., Parakeet)
    align_task = None
    skip_alignment = engines["transcribe"] in NATIVE_WORD_TIMESTAMP_ENGINES
    if word_timestamps and not skip_alignment:
        align_task = Task(
            id=uuid4(),
            job_id=job_id,
            stage="align",
            engine_id=engines["align"],
            status=TaskStatus.PENDING,
            dependencies=[transcribe_task.id],
            config={"word_timestamps": True},
            input_uri=None,  # Will use audio from prepare, segments from transcribe
            output_uri=None,
            retries=0,
            max_retries=2,
            required=True,
        )
        tasks.append(align_task)

    # PII Detection (M26 - optional, after alignment/diarization)
    pii_detect_task = None
    audio_redact_task = None
    pii_detection_enabled = parameters.get("pii_detection", False)

    if pii_detection_enabled:
        # PII detection requires word timestamps for audio timing
        # Depends on align (if present) or transcribe, plus diarize (if present)
        pii_dependencies = [transcribe_task.id]
        if align_task is not None:
            pii_dependencies.append(align_task.id)
        if diarize_task is not None:
            pii_dependencies.append(diarize_task.id)

        # Get PII detection config
        pii_detect_config = {
            "entity_types": parameters.get("pii_entity_types"),
            "confidence_threshold": parameters.get("pii_confidence_threshold", 0.5),
        }

        pii_detect_task = Task(
            id=uuid4(),
            job_id=job_id,
            stage="pii_detect",
            engine_id=engines["pii_detect"],
            status=TaskStatus.PENDING,
            dependencies=pii_dependencies,
            config=pii_detect_config,
            input_uri=None,
            output_uri=None,
            retries=0,
            max_retries=2,
            required=True,
        )
        tasks.append(pii_detect_task)

        # Audio redaction (optional, after PII detection)
        if parameters.get("redact_pii_audio", False):
            redaction_mode = parameters.get("pii_redaction_mode", "silence")
            if redaction_mode not in VALID_PII_REDACTION_MODES:
                logger.warning(
                    f"Unknown pii_redaction_mode '{redaction_mode}', "
                    f"expected one of {VALID_PII_REDACTION_MODES}. Defaulting to 'silence'."
                )
                redaction_mode = "silence"

            audio_redact_config = {
                "redaction_mode": redaction_mode,
                "buffer_ms": parameters.get("pii_buffer_ms", 50),
            }

            audio_redact_task = Task(
                id=uuid4(),
                job_id=job_id,
                stage="audio_redact",
                engine_id=engines["audio_redact"],
                status=TaskStatus.PENDING,
                dependencies=[pii_detect_task.id],
                config=audio_redact_config,
                input_uri=None,
                output_uri=None,
                retries=0,
                max_retries=2,
                required=True,
            )
            tasks.append(audio_redact_task)

    # Final task: Merge (depends on prepare, transcribe, optionally align, diarize, and PII)
    # Combines outputs into final transcript format
    merge_dependencies = [prepare_task.id, transcribe_task.id]
    if align_task is not None:
        merge_dependencies.append(align_task.id)
    if diarize_task is not None:
        merge_dependencies.append(diarize_task.id)
    if pii_detect_task is not None:
        merge_dependencies.append(pii_detect_task.id)
    if audio_redact_task is not None:
        merge_dependencies.append(audio_redact_task.id)

    merge_config = {
        "word_timestamps": word_timestamps,
        "speaker_detection": speaker_detection,
    }
    if pii_detection_enabled:
        merge_config["pii_detection"] = True

    merge_task = Task(
        id=uuid4(),
        job_id=job_id,
        stage="merge",
        engine_id=engines["merge"],
        status=TaskStatus.PENDING,
        dependencies=merge_dependencies,
        config=merge_config,
        input_uri=None,  # Will use previous task outputs
        output_uri=None,
        retries=0,
        max_retries=2,
        required=True,
    )
    tasks.append(merge_task)

    return tasks


def _build_per_channel_dag(
    tasks: list[Task],
    prepare_task: Task,
    job_id: UUID,
    engines: dict[str, str],
    transcribe_config: dict,
    word_timestamps: bool,
    num_channels: int = 2,
    parameters: dict | None = None,
) -> list[Task]:
    """Build DAG for per_channel speaker detection mode.

    Creates parallel processing pipelines for each audio channel:
        prepare (split_channels=true)
            ↓
        transcribe_ch0 → align_ch0 → pii_detect_ch0 → audio_redact_ch0 ─┐
                                                                         ├─ merge
        transcribe_ch1 → align_ch1 → pii_detect_ch1 → audio_redact_ch1 ─┘

    Args:
        tasks: List with prepare_task already added
        prepare_task: The prepare task (with split_channels=True)
        job_id: Job UUID
        engines: Engine ID mapping
        transcribe_config: Base transcription config
        word_timestamps: Whether to include alignment tasks
        num_channels: Number of audio channels (default: 2)
        parameters: Original job parameters (for PII config)

    Returns:
        Complete task list including merge
    """
    parameters = parameters or {}
    all_channel_tasks: list[Task] = []
    last_channel_tasks: list[Task] = []

    # Skip alignment for engines with native word timestamps (e.g., Parakeet)
    skip_alignment = engines["transcribe"] in NATIVE_WORD_TIMESTAMP_ENGINES

    # Check if PII detection is enabled
    pii_detection_enabled = parameters.get("pii_detection", False)
    redact_pii_audio = parameters.get("redact_pii_audio", False)

    for channel in range(num_channels):
        # Transcription task for this channel
        channel_transcribe_config = {
            **transcribe_config,
            "channel": channel,
        }

        transcribe_task = Task(
            id=uuid4(),
            job_id=job_id,
            stage=f"transcribe_ch{channel}",
            engine_id=engines["transcribe"],
            status=TaskStatus.PENDING,
            dependencies=[prepare_task.id],
            config=channel_transcribe_config,
            input_uri=None,
            output_uri=None,
            retries=0,
            max_retries=2,
            required=True,
        )
        tasks.append(transcribe_task)
        all_channel_tasks.append(transcribe_task)

        last_task = transcribe_task

        # Alignment task for this channel (optional)
        if word_timestamps and not skip_alignment:
            align_task = Task(
                id=uuid4(),
                job_id=job_id,
                stage=f"align_ch{channel}",
                engine_id=engines["align"],
                status=TaskStatus.PENDING,
                dependencies=[transcribe_task.id],
                config={"word_timestamps": True, "channel": channel},
                input_uri=None,
                output_uri=None,
                retries=0,
                max_retries=2,
                required=True,
            )
            tasks.append(align_task)
            all_channel_tasks.append(align_task)
            last_task = align_task

        # PII detection task for this channel
        if pii_detection_enabled:
            pii_detect_config = {
                "entity_types": parameters.get("pii_entity_types"),
                "confidence_threshold": parameters.get("pii_confidence_threshold", 0.5),
                "channel": channel,
            }

            pii_detect_task = Task(
                id=uuid4(),
                job_id=job_id,
                stage=f"pii_detect_ch{channel}",
                engine_id=engines["pii_detect"],
                status=TaskStatus.PENDING,
                dependencies=[last_task.id],
                config=pii_detect_config,
                input_uri=None,
                output_uri=None,
                retries=0,
                max_retries=2,
                required=True,
            )
            tasks.append(pii_detect_task)
            all_channel_tasks.append(pii_detect_task)
            last_task = pii_detect_task

            # Audio redaction task for this channel
            if redact_pii_audio:
                redaction_mode = parameters.get("pii_redaction_mode", "silence")
                if redaction_mode not in VALID_PII_REDACTION_MODES:
                    redaction_mode = "silence"

                audio_redact_config = {
                    "redaction_mode": redaction_mode,
                    "buffer_ms": parameters.get("pii_buffer_ms", 50),
                    "channel": channel,
                }

                audio_redact_task = Task(
                    id=uuid4(),
                    job_id=job_id,
                    stage=f"audio_redact_ch{channel}",
                    engine_id=engines["audio_redact"],
                    status=TaskStatus.PENDING,
                    dependencies=[pii_detect_task.id],
                    config=audio_redact_config,
                    input_uri=None,
                    output_uri=None,
                    retries=0,
                    max_retries=2,
                    required=True,
                )
                tasks.append(audio_redact_task)
                all_channel_tasks.append(audio_redact_task)
                last_task = audio_redact_task

        last_channel_tasks.append(last_task)

    # Merge depends on prepare and all per-channel tasks
    merge_dependencies = [prepare_task.id] + [t.id for t in all_channel_tasks]

    merge_config: dict = {
        "word_timestamps": word_timestamps,
        "speaker_detection": "per_channel",
        "channel_count": num_channels,
    }
    if pii_detection_enabled:
        merge_config["pii_detection"] = True
    if redact_pii_audio:
        merge_config["redact_pii_audio"] = True

    merge_task = Task(
        id=uuid4(),
        job_id=job_id,
        stage="merge",
        engine_id=engines["merge"],
        status=TaskStatus.PENDING,
        dependencies=merge_dependencies,
        config=merge_config,
        input_uri=None,
        output_uri=None,
        retries=0,
        max_retries=2,
        required=True,
    )
    tasks.append(merge_task)

    return tasks


# =============================================================================
# M31: Capability-Driven DAG Building
# =============================================================================


async def build_task_dag_async(
    job_id: UUID,
    audio_uri: str,
    parameters: dict,
    registry: BatchEngineRegistry,
    catalog: EngineCatalog,
) -> list[Task]:
    """Build a task DAG using capability-driven engine selection (M31).

    This is the preferred method for building DAGs. It uses the engine selector
    to choose engines based on capabilities rather than hardcoded defaults.

    The DAG shape is determined by selected engine capabilities:
    - If transcriber has supports_word_timestamps=True, skip align stage
    - If transcriber has includes_diarization=True, skip diarize stage

    Args:
        job_id: The job's UUID
        audio_uri: S3 URI to the audio file
        parameters: Job parameters (see build_task_dag for details)
        registry: Batch engine registry (running engines)
        catalog: Engine catalog (all available engines)

    Returns:
        List of Task objects with dependencies wired

    Raises:
        NoCapableEngineError: If no running engine can handle requirements
    """
    from dalston.orchestrator.engine_selector import select_pipeline_engines

    # Select engines for all required stages
    selections = await select_pipeline_engines(parameters, registry, catalog)

    # Build engines dict from selections (for compatibility with existing code)
    engines = {stage: sel.engine_id for stage, sel in selections.items()}

    # Determine DAG shape from capabilities
    skip_alignment = "align" not in selections
    skip_diarization = "diarize" not in selections

    # Log DAG shape decision
    logger.info(
        "dag_shape_decided",
        job_id=str(job_id),
        transcriber=selections["transcribe"].engine_id,
        alignment_included=not skip_alignment,
        diarization_included=not skip_diarization,
        stages=list(selections.keys()),
    )

    # Build the DAG using the same logic but with selected engines
    return _build_dag_with_engines(
        job_id=job_id,
        audio_uri=audio_uri,
        parameters=parameters,
        engines=engines,
        skip_alignment=skip_alignment,
        skip_diarization=skip_diarization,
    )


def _build_dag_with_engines(
    job_id: UUID,
    audio_uri: str,
    parameters: dict,
    engines: dict[str, str],
    skip_alignment: bool,
    skip_diarization: bool,
) -> list[Task]:
    """Build DAG with pre-selected engines.

    Internal function used by build_task_dag_async to create the actual
    task graph with capability-driven engine selection.

    Args:
        job_id: The job's UUID
        audio_uri: S3 URI to the audio file
        parameters: Job parameters
        engines: Pre-selected engine IDs by stage
        skip_alignment: Whether to skip the alignment stage
        skip_diarization: Whether to skip diarization even if requested

    Returns:
        List of Task objects with dependencies wired
    """
    # Check if word timestamps is enabled
    if "word_timestamps" in parameters:
        word_timestamps = parameters["word_timestamps"]
    elif "timestamps_granularity" in parameters:
        granularity = parameters["timestamps_granularity"]
        if granularity not in VALID_TIMESTAMPS_GRANULARITIES:
            logger.warning(
                f"Unknown timestamps_granularity '{granularity}', "
                f"expected one of {VALID_TIMESTAMPS_GRANULARITIES}. Defaulting to 'word'."
            )
            granularity = "word"
        word_timestamps = granularity == "word"
    else:
        word_timestamps = True

    # Check speaker detection mode
    speaker_detection = parameters.get("speaker_detection", "none")
    if speaker_detection not in VALID_SPEAKER_DETECTION_MODES:
        logger.warning(
            f"Unknown speaker_detection '{speaker_detection}', "
            f"expected one of {VALID_SPEAKER_DETECTION_MODES}. Defaulting to 'none'."
        )
        speaker_detection = "none"

    # Build diarization config
    diarize_config: dict = {}
    if parameters.get("num_speakers") is not None and parameters["num_speakers"] > 0:
        diarize_config["min_speakers"] = parameters["num_speakers"]
        diarize_config["max_speakers"] = parameters["num_speakers"]
    else:
        if (
            parameters.get("min_speakers") is not None
            and parameters["min_speakers"] > 0
        ):
            diarize_config["min_speakers"] = parameters["min_speakers"]
        if (
            parameters.get("max_speakers") is not None
            and parameters["max_speakers"] > 0
        ):
            diarize_config["max_speakers"] = parameters["max_speakers"]

    if parameters.get("exclusive"):
        diarize_config["exclusive"] = True

    # Build transcription config
    if "transcribe_config" in parameters:
        transcribe_config = {
            **DEFAULT_TRANSCRIBE_CONFIG,
            **parameters["transcribe_config"],
        }
        if parameters.get("language"):
            transcribe_config["language"] = parameters["language"]
    else:
        transcribe_config = {
            "model": parameters.get("model", DEFAULT_TRANSCRIBE_CONFIG["model"]),
            "language": parameters.get(
                "language", DEFAULT_TRANSCRIBE_CONFIG["language"]
            ),
            "beam_size": parameters.get(
                "beam_size", DEFAULT_TRANSCRIBE_CONFIG["beam_size"]
            ),
            "vad_filter": parameters.get(
                "vad_filter", DEFAULT_TRANSCRIBE_CONFIG["vad_filter"]
            ),
        }

    # M36: Add runtime_model_id if engine can be resolved in MODEL_REGISTRY
    # This allows the engine to load the specific model variant at runtime
    transcribe_engine = engines.get("transcribe", DEFAULT_ENGINES["transcribe"])
    model_info = resolve_model(transcribe_engine)
    if model_info is not None:
        # Override engine to runtime (e.g., "faster-whisper" instead of "faster-whisper-large-v3-turbo")
        engines["transcribe"] = model_info["runtime"]
        transcribe_config["runtime_model_id"] = model_info["runtime_model_id"]

    tasks: list[Task] = []
    diarize_task = None

    # Prepare config
    prepare_config: dict = {}
    if speaker_detection == "per_channel":
        prepare_config["split_channels"] = True

    # Task 1: Audio preparation
    prepare_task = Task(
        id=uuid4(),
        job_id=job_id,
        stage="prepare",
        engine_id=engines.get("prepare", DEFAULT_ENGINES["prepare"]),
        status=TaskStatus.PENDING,
        dependencies=[],
        config=prepare_config,
        input_uri=audio_uri,
        output_uri=None,
        retries=0,
        max_retries=2,
        required=True,
    )
    tasks.append(prepare_task)

    # Handle per_channel mode
    if speaker_detection == "per_channel":
        num_channels = parameters.get("num_channels", 2)
        return _build_per_channel_dag_with_engines(
            tasks=tasks,
            prepare_task=prepare_task,
            job_id=job_id,
            engines=engines,
            transcribe_config=transcribe_config,
            word_timestamps=word_timestamps,
            skip_alignment=skip_alignment,
            num_channels=num_channels,
            parameters=parameters,
        )

    # Diarization (if requested and not skipped due to native support)
    if speaker_detection == "diarize" and not skip_diarization:
        diarize_task = Task(
            id=uuid4(),
            job_id=job_id,
            stage="diarize",
            engine_id=engines.get("diarize", DEFAULT_ENGINES["diarize"]),
            status=TaskStatus.PENDING,
            dependencies=[prepare_task.id],
            config=diarize_config,
            input_uri=None,
            output_uri=None,
            retries=0,
            max_retries=2,
            required=True,
        )
        tasks.append(diarize_task)

    # Transcription
    transcribe_task = Task(
        id=uuid4(),
        job_id=job_id,
        stage="transcribe",
        engine_id=engines.get("transcribe", DEFAULT_ENGINES["transcribe"]),
        status=TaskStatus.PENDING,
        dependencies=[prepare_task.id],
        config=transcribe_config,
        input_uri=None,
        output_uri=None,
        retries=0,
        max_retries=2,
        required=True,
    )
    tasks.append(transcribe_task)

    # Alignment (if word timestamps wanted and engine doesn't have native support)
    align_task = None
    if word_timestamps and not skip_alignment:
        align_task = Task(
            id=uuid4(),
            job_id=job_id,
            stage="align",
            engine_id=engines.get("align", DEFAULT_ENGINES["align"]),
            status=TaskStatus.PENDING,
            dependencies=[transcribe_task.id],
            config={"word_timestamps": True},
            input_uri=None,
            output_uri=None,
            retries=0,
            max_retries=2,
            required=True,
        )
        tasks.append(align_task)

    # PII Detection
    pii_detect_task = None
    audio_redact_task = None
    pii_detection_enabled = parameters.get("pii_detection", False)

    if pii_detection_enabled:
        pii_dependencies = [transcribe_task.id]
        if align_task is not None:
            pii_dependencies.append(align_task.id)
        if diarize_task is not None:
            pii_dependencies.append(diarize_task.id)

        pii_detect_config = {
            "entity_types": parameters.get("pii_entity_types"),
            "confidence_threshold": parameters.get("pii_confidence_threshold", 0.5),
        }

        pii_detect_task = Task(
            id=uuid4(),
            job_id=job_id,
            stage="pii_detect",
            engine_id=engines.get("pii_detect", DEFAULT_ENGINES["pii_detect"]),
            status=TaskStatus.PENDING,
            dependencies=pii_dependencies,
            config=pii_detect_config,
            input_uri=None,
            output_uri=None,
            retries=0,
            max_retries=2,
            required=True,
        )
        tasks.append(pii_detect_task)

        if parameters.get("redact_pii_audio", False):
            redaction_mode = parameters.get("pii_redaction_mode", "silence")
            if redaction_mode not in VALID_PII_REDACTION_MODES:
                redaction_mode = "silence"

            audio_redact_config = {
                "redaction_mode": redaction_mode,
                "buffer_ms": parameters.get("pii_buffer_ms", 50),
            }

            audio_redact_task = Task(
                id=uuid4(),
                job_id=job_id,
                stage="audio_redact",
                engine_id=engines.get("audio_redact", DEFAULT_ENGINES["audio_redact"]),
                status=TaskStatus.PENDING,
                dependencies=[pii_detect_task.id],
                config=audio_redact_config,
                input_uri=None,
                output_uri=None,
                retries=0,
                max_retries=2,
                required=True,
            )
            tasks.append(audio_redact_task)

    # Merge
    merge_dependencies = [prepare_task.id, transcribe_task.id]
    if align_task is not None:
        merge_dependencies.append(align_task.id)
    if diarize_task is not None:
        merge_dependencies.append(diarize_task.id)
    if pii_detect_task is not None:
        merge_dependencies.append(pii_detect_task.id)
    if audio_redact_task is not None:
        merge_dependencies.append(audio_redact_task.id)

    merge_config: dict = {
        "word_timestamps": word_timestamps,
        "speaker_detection": speaker_detection,
    }
    if pii_detection_enabled:
        merge_config["pii_detection"] = True

    merge_task = Task(
        id=uuid4(),
        job_id=job_id,
        stage="merge",
        engine_id=engines.get("merge", DEFAULT_ENGINES["merge"]),
        status=TaskStatus.PENDING,
        dependencies=merge_dependencies,
        config=merge_config,
        input_uri=None,
        output_uri=None,
        retries=0,
        max_retries=2,
        required=True,
    )
    tasks.append(merge_task)

    return tasks


def _build_per_channel_dag_with_engines(
    tasks: list[Task],
    prepare_task: Task,
    job_id: UUID,
    engines: dict[str, str],
    transcribe_config: dict,
    word_timestamps: bool,
    skip_alignment: bool,
    num_channels: int = 2,
    parameters: dict | None = None,
) -> list[Task]:
    """Build per-channel DAG with pre-selected engines (M31).

    Creates parallel processing pipelines for each audio channel:
        prepare (split_channels=true)
            ↓
        transcribe_ch0 → align_ch0 → pii_detect_ch0 → audio_redact_ch0 ─┐
                                                                         ├─ merge
        transcribe_ch1 → align_ch1 → pii_detect_ch1 → audio_redact_ch1 ─┘

    Args:
        tasks: List with prepare_task already added
        prepare_task: The prepare task
        job_id: Job UUID
        engines: Pre-selected engine IDs by stage
        transcribe_config: Transcription configuration
        word_timestamps: Whether word timestamps are requested
        skip_alignment: Whether to skip alignment (transcriber has native support)
        num_channels: Number of audio channels
        parameters: Original job parameters (for PII config)

    Returns:
        Complete task list including merge
    """
    parameters = parameters or {}
    all_channel_tasks: list[Task] = []
    last_channel_tasks: list[Task] = []

    # Check if PII detection is enabled
    pii_detection_enabled = parameters.get("pii_detection", False)
    redact_pii_audio = parameters.get("redact_pii_audio", False)

    for channel in range(num_channels):
        channel_transcribe_config = {
            **transcribe_config,
            "channel": channel,
        }

        transcribe_task = Task(
            id=uuid4(),
            job_id=job_id,
            stage=f"transcribe_ch{channel}",
            engine_id=engines.get("transcribe", DEFAULT_ENGINES["transcribe"]),
            status=TaskStatus.PENDING,
            dependencies=[prepare_task.id],
            config=channel_transcribe_config,
            input_uri=None,
            output_uri=None,
            retries=0,
            max_retries=2,
            required=True,
        )
        tasks.append(transcribe_task)
        all_channel_tasks.append(transcribe_task)

        last_task = transcribe_task

        # Alignment task for this channel
        if word_timestamps and not skip_alignment:
            align_task = Task(
                id=uuid4(),
                job_id=job_id,
                stage=f"align_ch{channel}",
                engine_id=engines.get("align", DEFAULT_ENGINES["align"]),
                status=TaskStatus.PENDING,
                dependencies=[transcribe_task.id],
                config={"word_timestamps": True, "channel": channel},
                input_uri=None,
                output_uri=None,
                retries=0,
                max_retries=2,
                required=True,
            )
            tasks.append(align_task)
            all_channel_tasks.append(align_task)
            last_task = align_task

        # PII detection task for this channel
        if pii_detection_enabled:
            pii_detect_config = {
                "entity_types": parameters.get("pii_entity_types"),
                "confidence_threshold": parameters.get("pii_confidence_threshold", 0.5),
                "channel": channel,
            }

            pii_detect_task = Task(
                id=uuid4(),
                job_id=job_id,
                stage=f"pii_detect_ch{channel}",
                engine_id=engines.get("pii_detect", DEFAULT_ENGINES["pii_detect"]),
                status=TaskStatus.PENDING,
                dependencies=[last_task.id],
                config=pii_detect_config,
                input_uri=None,
                output_uri=None,
                retries=0,
                max_retries=2,
                required=True,
            )
            tasks.append(pii_detect_task)
            all_channel_tasks.append(pii_detect_task)
            last_task = pii_detect_task

            # Audio redaction task for this channel
            if redact_pii_audio:
                redaction_mode = parameters.get("pii_redaction_mode", "silence")
                if redaction_mode not in VALID_PII_REDACTION_MODES:
                    redaction_mode = "silence"

                audio_redact_config = {
                    "redaction_mode": redaction_mode,
                    "buffer_ms": parameters.get("pii_buffer_ms", 50),
                    "channel": channel,
                }

                audio_redact_task = Task(
                    id=uuid4(),
                    job_id=job_id,
                    stage=f"audio_redact_ch{channel}",
                    engine_id=engines.get(
                        "audio_redact", DEFAULT_ENGINES["audio_redact"]
                    ),
                    status=TaskStatus.PENDING,
                    dependencies=[pii_detect_task.id],
                    config=audio_redact_config,
                    input_uri=None,
                    output_uri=None,
                    retries=0,
                    max_retries=2,
                    required=True,
                )
                tasks.append(audio_redact_task)
                all_channel_tasks.append(audio_redact_task)
                last_task = audio_redact_task

        last_channel_tasks.append(last_task)

    # Merge depends on prepare and all per-channel tasks
    merge_dependencies = [prepare_task.id] + [t.id for t in all_channel_tasks]

    merge_config: dict = {
        "word_timestamps": word_timestamps,
        "speaker_detection": "per_channel",
        "channel_count": num_channels,
    }
    if pii_detection_enabled:
        merge_config["pii_detection"] = True
    if redact_pii_audio:
        merge_config["redact_pii_audio"] = True

    merge_task = Task(
        id=uuid4(),
        job_id=job_id,
        stage="merge",
        engine_id=engines.get("merge", DEFAULT_ENGINES["merge"]),
        status=TaskStatus.PENDING,
        dependencies=merge_dependencies,
        config=merge_config,
        input_uri=None,
        output_uri=None,
        retries=0,
        max_retries=2,
        required=True,
    )
    tasks.append(merge_task)

    return tasks
