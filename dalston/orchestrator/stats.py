"""Stats extraction from completed job transcripts.

Extracts summary statistics from the final transcript artifact
for storage on the job record.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger()


@dataclass
class JobResultStats:
    """Summary statistics extracted from a completed job's transcript."""

    language_code: str | None
    word_count: int
    segment_count: int
    speaker_count: int | None  # None when diarization wasn't run
    character_count: int


def extract_stats_from_transcript(transcript: dict[str, Any]) -> JobResultStats:
    """Extract summary statistics from a MergeOutput transcript.

    Args:
        transcript: The transcript dict (MergeOutput format) from the merge stage.

    Returns:
        JobResultStats with extracted statistics.
    """
    metadata = transcript.get("metadata", {})
    segments = transcript.get("segments", [])
    speakers = transcript.get("speakers", [])
    text = transcript.get("text", "")

    # Count segments
    segment_count = len(segments)

    # Count speakers - use metadata.speaker_count if available, else count speakers array
    # Return None if no speakers (diarization wasn't run or detected no speakers)
    raw_speaker_count = metadata.get("speaker_count", len(speakers))
    speaker_count = raw_speaker_count if raw_speaker_count > 0 else None

    # Count words - split text on whitespace
    word_count = len(text.split()) if text else 0

    # Count characters (excluding leading/trailing whitespace)
    character_count = len(text.strip()) if text else 0

    # Only extract language if transcript has content (empty transcripts have unreliable detection)
    language_code = metadata.get("language") if word_count > 0 else None

    logger.debug(
        "extracted_job_stats",
        language_code=language_code,
        word_count=word_count,
        segment_count=segment_count,
        speaker_count=speaker_count,
        character_count=character_count,
    )

    return JobResultStats(
        language_code=language_code,
        word_count=word_count,
        segment_count=segment_count,
        speaker_count=speaker_count,
        character_count=character_count,
    )
