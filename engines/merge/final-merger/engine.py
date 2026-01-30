"""Final merger engine for combining pipeline outputs.

Combines outputs from prepare and transcribe stages into the
standard Dalston transcript format with segment IDs and metadata.
"""

import logging
import os
from datetime import datetime, timezone

from dalston.engine_sdk import Engine, TaskInput, TaskOutput
from dalston.engine_sdk import io

logger = logging.getLogger(__name__)


class FinalMergerEngine(Engine):
    """Final merger engine that produces the canonical transcript output.

    Combines outputs from upstream stages:
    - prepare: audio metadata (duration, sample_rate, channels)
    - transcribe: text, segments, language

    Produces the standard transcript format with:
    - Segment IDs (seg_000, seg_001, ...)
    - Full metadata
    - Empty speakers array (populated by diarize in later milestones)
    """

    def process(self, input: TaskInput) -> TaskOutput:
        """Merge upstream outputs into final transcript.

        Args:
            input: Task input with previous_outputs from prepare and transcribe

        Returns:
            TaskOutput with merged transcript data
        """
        job_id = input.job_id

        # Extract outputs from upstream stages
        prepare_output = input.previous_outputs.get("prepare", {})
        transcribe_output = input.previous_outputs.get("transcribe", {})

        logger.info(f"Merging outputs for job {job_id}")
        logger.debug(f"Prepare output keys: {list(prepare_output.keys())}")
        logger.debug(f"Transcribe output keys: {list(transcribe_output.keys())}")

        # Extract audio metadata from prepare stage
        audio_duration = prepare_output.get("duration", 0.0)
        audio_channels = prepare_output.get("channels", 1)
        sample_rate = prepare_output.get("sample_rate", 16000)

        # Extract transcription data
        text = transcribe_output.get("text", "")
        raw_segments = transcribe_output.get("segments", [])
        language = transcribe_output.get("language", "en")
        language_probability = transcribe_output.get("language_probability", 1.0)

        # Build segments with IDs
        segments = []
        for idx, seg in enumerate(raw_segments):
            segment = {
                "id": f"seg_{idx:03d}",
                "start": seg.get("start", 0.0),
                "end": seg.get("end", 0.0),
                "text": seg.get("text", ""),
                "speaker": None,  # Will be populated by diarize stage
                "words": seg.get("words"),  # May be None or list of word objects
                "emotion": None,  # Will be populated by detect stage
                "emotion_confidence": None,
                "events": [],  # Will be populated by detect stage
            }
            segments.append(segment)

        # Determine pipeline stages that ran
        pipeline_stages = ["prepare", "transcribe", "merge"]

        # Build the final transcript structure
        transcript = {
            "job_id": str(job_id),
            "version": "1.0",
            "metadata": {
                "audio_duration": audio_duration,
                "audio_channels": audio_channels,
                "sample_rate": sample_rate,
                "language": language,
                "language_probability": round(language_probability, 3),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "pipeline_stages": pipeline_stages,
                "pipeline_warnings": [],
            },
            "text": text,
            "speakers": [],  # Empty for M02, populated by diarize in M04
            "segments": segments,
            "paragraphs": [],  # Empty for M02, populated by refine in M09
            "summary": None,  # Empty for M02, populated by refine in M09
        }

        logger.info(
            f"Merged transcript: {len(segments)} segments, "
            f"{len(text)} chars, language={language}"
        )

        # Write to the canonical transcript location for the Gateway
        s3_bucket = os.environ.get("S3_BUCKET", "dalston-artifacts")
        transcript_uri = f"s3://{s3_bucket}/jobs/{job_id}/transcript.json"
        io.upload_json(transcript, transcript_uri)
        logger.info(f"Uploaded transcript to {transcript_uri}")

        return TaskOutput(data=transcript)


if __name__ == "__main__":
    engine = FinalMergerEngine()
    engine.run()
