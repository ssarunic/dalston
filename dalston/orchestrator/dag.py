"""Task DAG builder for job expansion.

Converts job parameters into a directed acyclic graph of tasks.
Each task represents a processing step executed by a specific engine.
"""

from uuid import UUID, uuid4

from dalston.common.models import Task, TaskStatus


# Default engine IDs for each stage
DEFAULT_ENGINES = {
    "prepare": "audio-prepare",
    "transcribe": "faster-whisper",
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

    M02 implementation: Creates a 3-task pipeline:
        prepare (audio-prepare) -> transcribe (faster-whisper) -> merge (final-merger)

    Args:
        job_id: The job's UUID
        audio_uri: S3 URI to the audio file
        parameters: Job parameters including:
            - model: Whisper model size (default: large-v3)
            - language: Language code or None for auto-detect
            - beam_size: Beam search width (default: 5)
            - vad_filter: Enable VAD filtering (default: True)

    Returns:
        List of Task objects with dependencies wired
    """
    # Extract engine overrides from parameters (for testing/flexibility)
    engines = {
        "prepare": parameters.get("engine_prepare", DEFAULT_ENGINES["prepare"]),
        "transcribe": parameters.get("engine_transcribe", DEFAULT_ENGINES["transcribe"]),
        "merge": parameters.get("engine_merge", DEFAULT_ENGINES["merge"]),
    }

    # Build transcription config from parameters
    transcribe_config = {
        "model": parameters.get("model", DEFAULT_TRANSCRIBE_CONFIG["model"]),
        "language": parameters.get("language", DEFAULT_TRANSCRIBE_CONFIG["language"]),
        "beam_size": parameters.get("beam_size", DEFAULT_TRANSCRIBE_CONFIG["beam_size"]),
        "vad_filter": parameters.get("vad_filter", DEFAULT_TRANSCRIBE_CONFIG["vad_filter"]),
    }

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

    # Task 3: Merge (depends on prepare and transcribe)
    # Combines outputs into final transcript format
    merge_task = Task(
        id=uuid4(),
        job_id=job_id,
        stage="merge",
        engine_id=engines["merge"],
        status=TaskStatus.PENDING,
        dependencies=[prepare_task.id, transcribe_task.id],
        config={},
        input_uri=None,  # Will use previous task outputs
        output_uri=None,
        retries=0,
        max_retries=2,
        required=True,
    )

    return [prepare_task, transcribe_task, merge_task]
