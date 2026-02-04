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

# Valid values for speaker_detection API parameter
VALID_SPEAKER_DETECTION_MODES = {"none", "diarize", "per_channel"}


# Default engine IDs for each stage
DEFAULT_ENGINES = {
    "prepare": "audio-prepare",
    "transcribe": "faster-whisper",
    "align": "whisperx-align",
    "diarize": "pyannote-4.0",
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
    transcribe_config = {
        "model": parameters.get("model", DEFAULT_TRANSCRIBE_CONFIG["model"]),
        "language": parameters.get("language", DEFAULT_TRANSCRIBE_CONFIG["language"]),
        "beam_size": parameters.get(
            "beam_size", DEFAULT_TRANSCRIBE_CONFIG["beam_size"]
        ),
        "vad_filter": parameters.get(
            "vad_filter", DEFAULT_TRANSCRIBE_CONFIG["vad_filter"]
        ),
    }

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
        return _build_per_channel_dag(
            tasks=tasks,
            prepare_task=prepare_task,
            job_id=job_id,
            engines=engines,
            transcribe_config=transcribe_config,
            word_timestamps=word_timestamps,
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

    # Track the last task before merge (for merge dependencies)
    pre_merge_task = transcribe_task

    # Task (optional): Alignment (depends on transcribe)
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

    # Final task: Merge (depends on prepare, transcribe/align chain, and optionally diarize)
    # Combines outputs into final transcript format
    merge_dependencies = [prepare_task.id, pre_merge_task.id]
    if diarize_task is not None:
        merge_dependencies.append(diarize_task.id)

    merge_task = Task(
        id=uuid4(),
        job_id=job_id,
        stage="merge",
        engine_id=engines["merge"],
        status=TaskStatus.PENDING,
        dependencies=merge_dependencies,
        config={
            "word_timestamps": word_timestamps,
            "speaker_detection": speaker_detection,
        },
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
) -> list[Task]:
    """Build DAG for per_channel speaker detection mode.

    Creates parallel transcription (and optionally alignment) tasks
    for each audio channel. Assumes stereo input (2 channels).

    Args:
        tasks: List with prepare_task already added
        prepare_task: The prepare task (with split_channels=True)
        job_id: Job UUID
        engines: Engine ID mapping
        transcribe_config: Base transcription config
        word_timestamps: Whether to include alignment tasks

    Returns:
        Complete task list including merge
    """
    # Assume stereo (2 channels) for per_channel mode
    num_channels = 2
    pre_merge_tasks = []

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

        pre_merge_task = transcribe_task

        # Alignment task for this channel (optional)
        if word_timestamps:
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
            pre_merge_task = align_task

        pre_merge_tasks.append(pre_merge_task)

    # Merge task depends on prepare and all channel tasks
    merge_dependencies = [prepare_task.id] + [t.id for t in pre_merge_tasks]

    merge_task = Task(
        id=uuid4(),
        job_id=job_id,
        stage="merge",
        engine_id=engines["merge"],
        status=TaskStatus.PENDING,
        dependencies=merge_dependencies,
        config={
            "word_timestamps": word_timestamps,
            "speaker_detection": "per_channel",
            "channel_count": num_channels,
        },
        input_uri=None,
        output_uri=None,
        retries=0,
        max_retries=2,
        required=True,
    )
    tasks.append(merge_task)

    return tasks
