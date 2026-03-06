"""Task DAG builder for job expansion.

Converts job parameters into a directed acyclic graph of tasks.
Each task represents a processing step executed by a specific engine.

M31/M36: Uses capability-driven engine selection. The selector resolves
model IDs (e.g., "parakeet-tdt-1.1b") to runtime + runtime_model_id.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import structlog

import dalston.telemetry
from dalston.common.artifacts import ArtifactSelector, InputBinding
from dalston.common.models import Task, TaskStatus
from dalston.orchestrator.defaults import (
    DEFAULT_PII_BUFFER_MS,
    DEFAULT_PII_CONFIDENCE_THRESHOLD,
    DEFAULT_TASK_MAX_RETRIES,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from dalston.orchestrator.catalog import EngineCatalog
    from dalston.orchestrator.registry import BatchEngineRegistry

logger = structlog.get_logger()

# Valid values for timestamps_granularity API parameter
VALID_TIMESTAMPS_GRANULARITIES = {"word", "segment", "none"}

# Valid values for speaker_detection API parameter
VALID_SPEAKER_DETECTION_MODES = {"none", "diarize", "per_channel"}


def _audio_input_binding(
    *,
    producer_stage: str = "prepare",
    channel: int | None = None,
    role: str = "prepared",
) -> list[dict]:
    """Create a single audio slot binding for a task."""
    binding = InputBinding(
        slot="audio",
        selector=ArtifactSelector(
            producer_stage=producer_stage,
            kind="audio",
            channel=channel,
            role=role,
            required=True,
        ),
    )
    return [binding.model_dump(exclude_none=True)]


# Default engine IDs for each stage (runtime IDs, not model variant IDs)
# The runtime_model_id is passed separately in task config
DEFAULT_ENGINES = {
    "prepare": "audio-prepare",
    "transcribe": "faster-whisper",
    "align": "phoneme-align",
    "diarize": "pyannote-4.0",
    "pii_detect": "pii-presidio",
    "audio_redact": "audio-redactor",
    "merge": "final-merger",
}

# Valid PII redaction modes
VALID_PII_REDACTION_MODES = {"silence", "beep"}

# Default transcription config
DEFAULT_TRANSCRIBE_CONFIG = {
    "language": None,  # Auto-detect
    "beam_size": 5,
    "vad_filter": True,
}

# =============================================================================
# Capability-Driven DAG Building (M31/M36)
# =============================================================================
# Model resolution now happens in engine_selector.py via the catalog.
# The selector resolves model IDs (e.g., "parakeet-tdt-1.1b") to:
# - runtime: The engine runtime (e.g., "nemo", "faster-whisper")
# - runtime_model_id: The model ID passed to the underlying library
# This information flows through EngineSelectionResult to the DAG builder.


async def build_task_dag(
    job_id: UUID,
    audio_uri: str,
    parameters: dict,
    registry: BatchEngineRegistry,
    catalog: EngineCatalog,
    db: AsyncSession | None = None,
) -> list[Task]:
    """Build a task DAG for a job using capability-driven engine selection.

    Creates a pipeline with optional speaker detection:

    Mode: none (default)
        prepare → transcribe → [align] → merge

    Mode: diarize
        prepare → transcribe → [align] → merge
                ↘ diarize ─────────────↗

    Mode: per_channel (stereo audio)
        prepare ─┬→ transcribe_ch0 → [align_ch0] ─┬→ merge
                 └→ transcribe_ch1 → [align_ch1] ─┘

    The DAG shape is determined by selected engine capabilities:
    - If transcriber has supports_word_timestamps=True, skip align stage
    - If transcriber has includes_diarization=True, skip diarize stage

    Args:
        job_id: The job's UUID
        audio_uri: S3 URI to the audio file
        parameters: Job parameters including:
            - model: Model ID (e.g., "parakeet-tdt-1.1b") or "auto"
            - language: Language code or "auto" for detection
            - speaker_detection: "none", "diarize", or "per_channel"
            - timestamps_granularity: "word", "segment", or "none"
            - num_speakers/min_speakers/max_speakers: Speaker hints
        registry: Batch engine registry (running engines)
        catalog: Engine catalog (all available engines)
        db: Optional database session for HF model lookup

    Returns:
        List of Task objects with dependencies wired

    Raises:
        NoCapableEngineError: If no running engine can handle requirements
    """
    from dalston.orchestrator.engine_selector import select_pipeline_engines

    # Select engines for all required stages
    selection = await select_pipeline_engines(parameters, registry, catalog, db=db)
    selections = selection.stages

    # Build engines dict from selections
    engines = {stage: sel.runtime for stage, sel in selections.items()}

    # Extract stage runtime model IDs from selections.
    transcribe_selection = selections["transcribe"]
    stage_runtime_model_ids = {
        stage: sel.runtime_model_id
        for stage, sel in selections.items()
        if sel.runtime_model_id is not None
    }
    runtime_model_id = stage_runtime_model_ids.get("transcribe")

    # Determine DAG shape from capabilities
    skip_alignment = "align" not in selections
    skip_diarization = "diarize" not in selections

    # Log DAG shape decision
    logger.info(
        "dag_shape_decided",
        job_id=str(job_id),
        transcriber=transcribe_selection.runtime,
        stage_runtime_model_ids=stage_runtime_model_ids,
        alignment_included=not skip_alignment,
        diarization_included=not skip_diarization,
        stages=list(selections.keys()),
    )

    # Build the DAG with selected engines
    with dalston.telemetry.create_span(
        "orchestrator.dag_build",
        attributes={
            "dalston.job_id": str(job_id),
            "dalston.runtime": transcribe_selection.runtime,
            "dalston.model": runtime_model_id or "",
            "dalston.dag.stages": list(selections.keys()),
            "dalston.dag.task_count": len(selections),
            "dalston.dag.has_alignment": not skip_alignment,
            "dalston.dag.has_diarization": not skip_diarization,
        },
    ):
        tasks = _build_dag_with_engines(
            job_id=job_id,
            audio_uri=audio_uri,
            parameters=selection.effective_parameters,
            engines=engines,
            skip_alignment=skip_alignment,
            skip_diarization=skip_diarization,
            runtime_model_id=runtime_model_id,
            stage_runtime_model_ids=stage_runtime_model_ids,
        )
        dalston.telemetry.set_span_attribute("dalston.dag.task_count", len(tasks))
        return tasks


def _build_dag_with_engines(
    job_id: UUID,
    audio_uri: str,
    parameters: dict,
    engines: dict[str, str],
    skip_alignment: bool,
    skip_diarization: bool,
    runtime_model_id: str | None = None,
    stage_runtime_model_ids: dict[str, str] | None = None,
) -> list[Task]:
    """Build DAG with pre-selected engines.

    Internal function used by build_task_dag to create the actual
    task graph with capability-driven engine selection.

    Args:
        job_id: The job's UUID
        audio_uri: S3 URI to the audio file
        parameters: Job parameters
        engines: Pre-selected engine IDs by stage (runtime IDs like "nemo", "faster-whisper")
        skip_alignment: Whether to skip the alignment stage
        skip_diarization: Whether to skip diarization even if requested
        runtime_model_id: Model ID to pass to the transcription engine
                         (e.g., "nvidia/parakeet-tdt-1.1b"). Already resolved by selector.
        stage_runtime_model_ids: Runtime model IDs keyed by stage
            (e.g., {"transcribe": "...", "align": "..."}).

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

    # M36: Set runtime_model_id if user requested a specific model variant
    # The selector already resolved model ID → (runtime, runtime_model_id)
    stage_runtime_model_ids = dict(stage_runtime_model_ids or {})
    if runtime_model_id is not None and "transcribe" not in stage_runtime_model_ids:
        stage_runtime_model_ids["transcribe"] = runtime_model_id
    if "transcribe" in stage_runtime_model_ids:
        transcribe_config["runtime_model_id"] = stage_runtime_model_ids["transcribe"]

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
        runtime=engines.get("prepare", DEFAULT_ENGINES["prepare"]),
        status=TaskStatus.PENDING,
        dependencies=[],
        input_bindings=[],
        config=prepare_config,
        input_uri=audio_uri,
        output_uri=None,
        retries=0,
        max_retries=DEFAULT_TASK_MAX_RETRIES,
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
            stage_runtime_model_ids=stage_runtime_model_ids,
        )

    # Diarization (if requested and not skipped due to native support)
    if speaker_detection == "diarize" and not skip_diarization:
        if "diarize" in stage_runtime_model_ids:
            diarize_config["runtime_model_id"] = stage_runtime_model_ids["diarize"]
        diarize_task = Task(
            id=uuid4(),
            job_id=job_id,
            stage="diarize",
            runtime=engines.get("diarize", DEFAULT_ENGINES["diarize"]),
            status=TaskStatus.PENDING,
            dependencies=[prepare_task.id],
            input_bindings=_audio_input_binding(),
            config=diarize_config,
            input_uri=None,
            output_uri=None,
            retries=0,
            max_retries=DEFAULT_TASK_MAX_RETRIES,
            required=True,
        )
        tasks.append(diarize_task)

    # Transcription
    transcribe_task = Task(
        id=uuid4(),
        job_id=job_id,
        stage="transcribe",
        runtime=engines.get("transcribe", DEFAULT_ENGINES["transcribe"]),
        status=TaskStatus.PENDING,
        dependencies=[prepare_task.id],
        input_bindings=_audio_input_binding(),
        config=transcribe_config,
        input_uri=None,
        output_uri=None,
        retries=0,
        max_retries=DEFAULT_TASK_MAX_RETRIES,
        required=True,
    )
    tasks.append(transcribe_task)

    # Alignment (if word timestamps wanted and engine doesn't have native support)
    align_task = None
    if word_timestamps and not skip_alignment:
        align_config = {"word_timestamps": True}
        if "align" in stage_runtime_model_ids:
            align_config["runtime_model_id"] = stage_runtime_model_ids["align"]
        align_task = Task(
            id=uuid4(),
            job_id=job_id,
            stage="align",
            runtime=engines.get("align", DEFAULT_ENGINES["align"]),
            status=TaskStatus.PENDING,
            dependencies=[transcribe_task.id],
            input_bindings=_audio_input_binding(),
            config=align_config,
            input_uri=None,
            output_uri=None,
            retries=0,
            max_retries=DEFAULT_TASK_MAX_RETRIES,
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
            "confidence_threshold": parameters.get(
                "pii_confidence_threshold", DEFAULT_PII_CONFIDENCE_THRESHOLD
            ),
        }
        if "pii_detect" in stage_runtime_model_ids:
            pii_detect_config["runtime_model_id"] = stage_runtime_model_ids[
                "pii_detect"
            ]

        pii_detect_task = Task(
            id=uuid4(),
            job_id=job_id,
            stage="pii_detect",
            runtime=engines.get("pii_detect", DEFAULT_ENGINES["pii_detect"]),
            status=TaskStatus.PENDING,
            dependencies=pii_dependencies,
            input_bindings=[],
            config=pii_detect_config,
            input_uri=None,
            output_uri=None,
            retries=0,
            max_retries=DEFAULT_TASK_MAX_RETRIES,
            required=True,
        )
        tasks.append(pii_detect_task)

        if parameters.get("redact_pii_audio", False):
            redaction_mode = parameters.get("pii_redaction_mode", "silence")
            if redaction_mode not in VALID_PII_REDACTION_MODES:
                redaction_mode = "silence"

            audio_redact_config = {
                "redaction_mode": redaction_mode,
                "buffer_ms": parameters.get("pii_buffer_ms", DEFAULT_PII_BUFFER_MS),
            }

            audio_redact_task = Task(
                id=uuid4(),
                job_id=job_id,
                stage="audio_redact",
                runtime=engines.get("audio_redact", DEFAULT_ENGINES["audio_redact"]),
                status=TaskStatus.PENDING,
                dependencies=[pii_detect_task.id],
                input_bindings=_audio_input_binding(),
                config=audio_redact_config,
                input_uri=None,
                output_uri=None,
                retries=0,
                max_retries=DEFAULT_TASK_MAX_RETRIES,
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
    merge_bindings: list[dict] = []
    if audio_redact_task is not None:
        merge_bindings.extend(
            _audio_input_binding(producer_stage="audio_redact", role="redacted")
        )

    merge_task = Task(
        id=uuid4(),
        job_id=job_id,
        stage="merge",
        runtime=engines.get("merge", DEFAULT_ENGINES["merge"]),
        status=TaskStatus.PENDING,
        dependencies=merge_dependencies,
        input_bindings=merge_bindings,
        config=merge_config,
        input_uri=None,
        output_uri=None,
        retries=0,
        max_retries=DEFAULT_TASK_MAX_RETRIES,
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
    stage_runtime_model_ids: dict[str, str] | None = None,
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
    stage_runtime_model_ids = stage_runtime_model_ids or {}
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
            runtime=engines.get("transcribe", DEFAULT_ENGINES["transcribe"]),
            status=TaskStatus.PENDING,
            dependencies=[prepare_task.id],
            input_bindings=_audio_input_binding(channel=channel),
            config=channel_transcribe_config,
            input_uri=None,
            output_uri=None,
            retries=0,
            max_retries=DEFAULT_TASK_MAX_RETRIES,
            required=True,
        )
        tasks.append(transcribe_task)
        all_channel_tasks.append(transcribe_task)

        last_task = transcribe_task

        # Alignment task for this channel
        if word_timestamps and not skip_alignment:
            align_config = {"word_timestamps": True, "channel": channel}
            if "align" in stage_runtime_model_ids:
                align_config["runtime_model_id"] = stage_runtime_model_ids["align"]
            align_task = Task(
                id=uuid4(),
                job_id=job_id,
                stage=f"align_ch{channel}",
                runtime=engines.get("align", DEFAULT_ENGINES["align"]),
                status=TaskStatus.PENDING,
                dependencies=[transcribe_task.id],
                input_bindings=_audio_input_binding(channel=channel),
                config=align_config,
                input_uri=None,
                output_uri=None,
                retries=0,
                max_retries=DEFAULT_TASK_MAX_RETRIES,
                required=True,
            )
            tasks.append(align_task)
            all_channel_tasks.append(align_task)
            last_task = align_task

        # PII detection task for this channel
        if pii_detection_enabled:
            pii_detect_config = {
                "entity_types": parameters.get("pii_entity_types"),
                "confidence_threshold": parameters.get(
                    "pii_confidence_threshold", DEFAULT_PII_CONFIDENCE_THRESHOLD
                ),
                "channel": channel,
            }
            if "pii_detect" in stage_runtime_model_ids:
                pii_detect_config["runtime_model_id"] = stage_runtime_model_ids[
                    "pii_detect"
                ]

            pii_detect_task = Task(
                id=uuid4(),
                job_id=job_id,
                stage=f"pii_detect_ch{channel}",
                runtime=engines.get("pii_detect", DEFAULT_ENGINES["pii_detect"]),
                status=TaskStatus.PENDING,
                dependencies=[last_task.id],
                input_bindings=[],
                config=pii_detect_config,
                input_uri=None,
                output_uri=None,
                retries=0,
                max_retries=DEFAULT_TASK_MAX_RETRIES,
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
                    "buffer_ms": parameters.get("pii_buffer_ms", DEFAULT_PII_BUFFER_MS),
                    "channel": channel,
                }

                audio_redact_task = Task(
                    id=uuid4(),
                    job_id=job_id,
                    stage=f"audio_redact_ch{channel}",
                    runtime=engines.get(
                        "audio_redact", DEFAULT_ENGINES["audio_redact"]
                    ),
                    status=TaskStatus.PENDING,
                    dependencies=[pii_detect_task.id],
                    input_bindings=_audio_input_binding(channel=channel),
                    config=audio_redact_config,
                    input_uri=None,
                    output_uri=None,
                    retries=0,
                    max_retries=DEFAULT_TASK_MAX_RETRIES,
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
    merge_bindings: list[dict] = []
    if redact_pii_audio:
        for channel in range(num_channels):
            merge_bindings.append(
                InputBinding(
                    slot=f"redacted_audio_ch{channel}",
                    selector=ArtifactSelector(
                        producer_stage=f"audio_redact_ch{channel}",
                        kind="audio",
                        channel=channel,
                        role="redacted",
                        required=True,
                    ),
                ).model_dump(exclude_none=True)
            )

    merge_task = Task(
        id=uuid4(),
        job_id=job_id,
        stage="merge",
        runtime=engines.get("merge", DEFAULT_ENGINES["merge"]),
        status=TaskStatus.PENDING,
        dependencies=merge_dependencies,
        input_bindings=merge_bindings,
        config=merge_config,
        input_uri=None,
        output_uri=None,
        retries=0,
        max_retries=DEFAULT_TASK_MAX_RETRIES,
        required=True,
    )
    tasks.append(merge_task)

    return tasks
