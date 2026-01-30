"""Task DAG builder for job expansion.

Converts job parameters into a directed acyclic graph of tasks.
Each task represents a processing step executed by a specific engine.
"""

import logging
from uuid import UUID, uuid4

from dalston.common.models import Task, TaskStatus

logger = logging.getLogger(__name__)

# Valid values for timestamps_granularity API parameter
VALID_TIMESTAMPS_GRANULARITIES = {"word", "segment", "none"}


# Default engine IDs for each stage
DEFAULT_ENGINES = {
    "prepare": "audio-prepare",
    "transcribe": "faster-whisper",
    "align": "whisperx-align",
    "merge": "final-merger",
}

# Default transcription config
DEFAULT_TRANSCRIBE_CONFIG = {
    "model": "large-v3",
    "language": None,  # Auto-detect
    "beam_size": 5,
    "vad_filter": True,
}


def build_task_dag(job_id: UUID, audio_uri: str, parameters: dict) -> list[Task]:
    """Build a task DAG for a job.

    M03 implementation: Creates a 3-4 task pipeline:
        prepare → transcribe → [align] → merge

    Alignment is included by default for word-level timestamps.

    Args:
        job_id: The job's UUID
        audio_uri: S3 URI to the audio file
        parameters: Job parameters including:
            - model: Whisper model size (default: large-v3)
            - language: Language code or None for auto-detect
            - beam_size: Beam search width (default: 5)
            - vad_filter: Enable VAD filtering (default: True)
            - word_timestamps: Enable word-level alignment (default: True)

    Returns:
        List of Task objects with dependencies wired
    """
    # Extract engine overrides from parameters (for testing/flexibility)
    engines = {
        "prepare": parameters.get("engine_prepare", DEFAULT_ENGINES["prepare"]),
        "transcribe": parameters.get("engine_transcribe", DEFAULT_ENGINES["transcribe"]),
        "align": parameters.get("engine_align", DEFAULT_ENGINES["align"]),
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

    # Build transcription config from parameters
    transcribe_config = {
        "model": parameters.get("model", DEFAULT_TRANSCRIBE_CONFIG["model"]),
        "language": parameters.get("language", DEFAULT_TRANSCRIBE_CONFIG["language"]),
        "beam_size": parameters.get("beam_size", DEFAULT_TRANSCRIBE_CONFIG["beam_size"]),
        "vad_filter": parameters.get("vad_filter", DEFAULT_TRANSCRIBE_CONFIG["vad_filter"]),
    }

    tasks = []

    # Task 1: Audio preparation (no dependencies)
    # Converts uploaded audio to 16kHz mono WAV
    prepare_task = Task(
        id=uuid4(),
        job_id=job_id,
        stage="prepare",
        engine_id=engines["prepare"],
        status=TaskStatus.PENDING,
        dependencies=[],
        config={},  # audio-prepare uses defaults
        input_uri=audio_uri,
        output_uri=None,
        retries=0,
        max_retries=2,
        required=True,
    )
    tasks.append(prepare_task)

    # Task 2: Transcription (depends on prepare)
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

    # Track the last task before merge (for merge dependencies)
    pre_merge_task = transcribe_task

    # Task 3 (optional): Alignment (depends on transcribe)
    # Adds precise word-level timestamps using wav2vec2 forced alignment
    if word_timestamps:
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
        pre_merge_task = align_task

    # Task 4 (or 3): Merge (depends on prepare and last processing task)
    # Combines outputs into final transcript format
    merge_task = Task(
        id=uuid4(),
        job_id=job_id,
        stage="merge",
        engine_id=engines["merge"],
        status=TaskStatus.PENDING,
        dependencies=[prepare_task.id, pre_merge_task.id],
        config={"word_timestamps": word_timestamps},
        input_uri=None,  # Will use previous task outputs
        output_uri=None,
        retries=0,
        max_retries=2,
        required=True,
    )
    tasks.append(merge_task)

    return tasks
