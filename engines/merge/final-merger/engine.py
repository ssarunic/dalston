"""Final merger engine for combining pipeline outputs.

Combines outputs from prepare, transcribe, and optionally align stages
into the standard Dalston transcript format with segment IDs and metadata.
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
    - align (optional): segments with precise word-level timestamps

    Produces the standard transcript format with:
    - Segment IDs (seg_000, seg_001, ...)
    - Full metadata including word_timestamps flag
    - Empty speakers array (populated by diarize in later milestones)
    """

    def process(self, input: TaskInput) -> TaskOutput:
        """Merge upstream outputs into final transcript.

        Args:
            input: Task input with previous_outputs from prepare, transcribe,
                   and optionally align stages

        Returns:
            TaskOutput with merged transcript data
        """
        job_id = input.job_id
        config = input.config

        # Extract outputs from upstream stages
        prepare_output = input.previous_outputs.get("prepare", {})
        transcribe_output = input.previous_outputs.get("transcribe", {})
        align_output = input.previous_outputs.get("align")

        logger.info(f"Merging outputs for job {job_id}")
        logger.debug(f"Prepare output keys: {list(prepare_output.keys())}")
        logger.debug(f"Transcribe output keys: {list(transcribe_output.keys())}")
        if align_output:
            logger.debug(f"Align output keys: {list(align_output.keys())}")

        # Extract audio metadata from prepare stage
        audio_duration = prepare_output.get("duration", 0.0)
        audio_channels = prepare_output.get("channels", 1)
        sample_rate = prepare_output.get("sample_rate", 16000)

        # Determine which output to use for segments
        # Use aligned segments if alignment ran successfully, otherwise transcribe
        pipeline_warnings = []
        word_timestamps_requested = config.get("word_timestamps", False)

        if align_output:
            # Check if alignment produced a warning (graceful degradation)
            align_warning = align_output.get("warning")
            if align_warning:
                logger.warning(f"Alignment warning: {align_warning.get('reason')}")
                pipeline_warnings.append(align_warning)
                # Use transcribe segments as fallback
                raw_segments = transcribe_output.get("segments", [])
                word_timestamps_available = False
            else:
                # Use aligned segments
                raw_segments = align_output.get("segments", [])
                word_timestamps_available = align_output.get("word_timestamps", True)
                logger.info("Using aligned segments with word-level timestamps")
        else:
            # No alignment stage ran
            raw_segments = transcribe_output.get("segments", [])
            word_timestamps_available = False

        # Extract other transcription data
        text = transcribe_output.get("text", "")
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
                "words": seg.get("words") if word_timestamps_available else None,
                "emotion": None,  # Will be populated by detect stage
                "emotion_confidence": None,
                "events": [],  # Will be populated by detect stage
            }
            segments.append(segment)

        # Determine pipeline stages that ran
        pipeline_stages = ["prepare", "transcribe"]
        if align_output:
            pipeline_stages.append("align")
        pipeline_stages.append("merge")

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
                "word_timestamps": word_timestamps_available,
                "word_timestamps_requested": word_timestamps_requested,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "pipeline_stages": pipeline_stages,
                "pipeline_warnings": pipeline_warnings,
            },
            "text": text,
            "speakers": [],  # Empty for M03, populated by diarize in M04
            "segments": segments,
            "paragraphs": [],  # Empty for M03, populated by refine in M09
            "summary": None,  # Empty for M03, populated by refine in M09
        }

        logger.info(
            f"Merged transcript: {len(segments)} segments, "
            f"{len(text)} chars, language={language}, "
            f"word_timestamps={word_timestamps_available}"
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
