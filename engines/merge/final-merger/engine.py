"""Final merger engine for combining pipeline outputs.

Combines outputs from prepare, transcribe, align, and diarize stages
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
    - diarize (optional): speaker diarization segments

    Produces the standard transcript format with:
    - Segment IDs (seg_000, seg_001, ...)
    - Full metadata including word_timestamps flag
    - Speaker assignments based on diarization overlap
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

        # Get speaker detection mode from config
        speaker_detection = config.get("speaker_detection", "none")

        logger.info(f"Merging outputs for job {job_id}")
        logger.debug(f"Prepare output keys: {list(prepare_output.keys())}")

        # Handle per_channel mode separately
        if speaker_detection == "per_channel":
            return self._merge_per_channel(input, prepare_output, config)

        # Standard mode: single transcribe/align/diarize outputs
        transcribe_output = input.previous_outputs.get("transcribe", {})
        align_output = input.previous_outputs.get("align")
        diarize_output = input.previous_outputs.get("diarize")

        logger.debug(f"Transcribe output keys: {list(transcribe_output.keys())}")
        if align_output:
            logger.debug(f"Align output keys: {list(align_output.keys())}")
        if diarize_output:
            logger.debug(f"Diarize output keys: {list(diarize_output.keys())}")

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
        language_confidence = transcribe_output.get("language_confidence", 1.0)

        # Extract diarization data if available
        diarization_segments = []
        diarization_speakers = []
        diarization_warning = None

        if diarize_output and speaker_detection == "diarize":
            diarization_warning = diarize_output.get("warning")
            if diarization_warning:
                logger.warning(f"Diarization warning: {diarization_warning.get('reason')}")
                pipeline_warnings.append(diarization_warning)
            else:
                diarization_segments = diarize_output.get("diarization_segments", [])
                diarization_speakers = diarize_output.get("speakers", [])
                logger.info(
                    f"Using diarization: {len(diarization_speakers)} speakers, "
                    f"{len(diarization_segments)} segments"
                )

        # Build segments with IDs and speaker assignments
        segments = []
        for idx, seg in enumerate(raw_segments):
            seg_start = seg.get("start", 0.0)
            seg_end = seg.get("end", 0.0)

            # Assign speaker based on diarization overlap
            speaker = None
            if diarization_segments:
                speaker = self._find_speaker_by_overlap(
                    seg_start, seg_end, diarization_segments
                )

            segment = {
                "id": f"seg_{idx:03d}",
                "start": seg_start,
                "end": seg_end,
                "text": seg.get("text", ""),
                "speaker": speaker,
                "words": seg.get("words") if word_timestamps_available else None,
                "emotion": None,  # Will be populated by detect stage
                "emotion_confidence": None,
                "events": [],  # Will be populated by detect stage
            }
            segments.append(segment)

        # Build speakers array
        speakers = []
        if diarization_speakers:
            for speaker_id in diarization_speakers:
                speakers.append({
                    "id": speaker_id,
                    "label": None,  # User can assign labels later
                })
            logger.info(f"Built speakers array with {len(speakers)} speakers")

        # Determine pipeline stages that ran
        pipeline_stages = ["prepare", "transcribe"]
        if align_output:
            pipeline_stages.append("align")
        if diarize_output and speaker_detection == "diarize":
            pipeline_stages.append("diarize")
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
                "language_confidence": round(language_confidence, 3),
                "word_timestamps": word_timestamps_available,
                "word_timestamps_requested": word_timestamps_requested,
                "speaker_detection": speaker_detection,
                "speaker_count": len(speakers),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "pipeline_stages": pipeline_stages,
                "pipeline_warnings": pipeline_warnings,
            },
            "text": text,
            "speakers": speakers,
            "segments": segments,
            "paragraphs": [],  # Empty for now, populated by refine in later milestones
            "summary": None,  # Empty for now, populated by refine in later milestones
        }

        logger.info(
            f"Merged transcript: {len(segments)} segments, "
            f"{len(text)} chars, language={language}, "
            f"word_timestamps={word_timestamps_available}, "
            f"speakers={len(speakers)}"
        )

        # Write to the canonical transcript location for the Gateway
        s3_bucket = os.environ.get("S3_BUCKET", "dalston-artifacts")
        transcript_uri = f"s3://{s3_bucket}/jobs/{job_id}/transcript.json"
        io.upload_json(transcript, transcript_uri)
        logger.info(f"Uploaded transcript to {transcript_uri}")

        return TaskOutput(data=transcript)

    def _merge_per_channel(
        self,
        input: TaskInput,
        prepare_output: dict,
        config: dict,
    ) -> TaskOutput:
        """Merge transcripts from per-channel processing.

        Interleaves segments from multiple channel transcripts by timestamp,
        assigning speakers based on channel (SPEAKER_00 for ch0, etc.).

        Args:
            input: Task input with previous_outputs containing channel data
            prepare_output: Output from prepare stage
            config: Merge task config

        Returns:
            TaskOutput with merged transcript
        """
        job_id = input.job_id
        word_timestamps = config.get("word_timestamps", False)
        channel_count = config.get("channel_count", 2)

        logger.info(f"Merging per-channel outputs: {channel_count} channels")

        # Extract audio metadata
        audio_duration = prepare_output.get("duration", 0.0)
        audio_channels = prepare_output.get("original_channels", 2)
        sample_rate = prepare_output.get("sample_rate", 16000)

        # Collect segments from all channels
        all_segments = []
        pipeline_warnings = []
        language = "en"
        language_confidence = 1.0

        for channel in range(channel_count):
            # Get transcribe output for this channel
            transcribe_key = f"transcribe_ch{channel}"
            align_key = f"align_ch{channel}"

            transcribe_output = input.previous_outputs.get(transcribe_key, {})
            align_output = input.previous_outputs.get(align_key)

            if not transcribe_output:
                logger.warning(f"Missing {transcribe_key} output")
                continue

            # Use first channel's language detection
            if channel == 0:
                language = transcribe_output.get("language", "en")
                language_confidence = transcribe_output.get("language_confidence", 1.0)

            # Get segments from align or transcribe
            if align_output and not align_output.get("warning"):
                raw_segments = align_output.get("segments", [])
                has_words = align_output.get("word_timestamps", True)
            else:
                raw_segments = transcribe_output.get("segments", [])
                has_words = False
                if align_output and align_output.get("warning"):
                    pipeline_warnings.append(align_output["warning"])

            # Add channel/speaker info to each segment
            speaker_id = f"SPEAKER_{channel:02d}"
            for seg in raw_segments:
                all_segments.append({
                    "start": seg.get("start", 0.0),
                    "end": seg.get("end", 0.0),
                    "text": seg.get("text", ""),
                    "speaker": speaker_id,
                    "words": seg.get("words") if has_words else None,
                    "channel": channel,
                })

        # Sort all segments by start time (interleave)
        all_segments.sort(key=lambda s: s["start"])

        # Build final segments with IDs
        segments = []
        for idx, seg in enumerate(all_segments):
            segment = {
                "id": f"seg_{idx:03d}",
                "start": seg["start"],
                "end": seg["end"],
                "text": seg["text"],
                "speaker": seg["speaker"],
                "words": seg.get("words"),
                "emotion": None,
                "emotion_confidence": None,
                "events": [],
            }
            segments.append(segment)

        # Build speakers array
        speakers = [
            {"id": f"SPEAKER_{ch:02d}", "label": None, "channel": ch}
            for ch in range(channel_count)
        ]

        # Combine text from all segments
        text = " ".join(seg["text"] for seg in segments if seg["text"])

        # Determine pipeline stages
        pipeline_stages = ["prepare"]
        for ch in range(channel_count):
            pipeline_stages.append(f"transcribe_ch{ch}")
            if word_timestamps:
                pipeline_stages.append(f"align_ch{ch}")
        pipeline_stages.append("merge")

        # Build transcript
        transcript = {
            "job_id": str(job_id),
            "version": "1.0",
            "metadata": {
                "audio_duration": audio_duration,
                "audio_channels": audio_channels,
                "sample_rate": sample_rate,
                "language": language,
                "language_confidence": round(language_confidence, 3),
                "word_timestamps": word_timestamps,
                "word_timestamps_requested": word_timestamps,
                "speaker_detection": "per_channel",
                "speaker_count": len(speakers),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "pipeline_stages": pipeline_stages,
                "pipeline_warnings": pipeline_warnings,
            },
            "text": text,
            "speakers": speakers,
            "segments": segments,
            "paragraphs": [],
            "summary": None,
        }

        logger.info(
            f"Merged per-channel transcript: {len(segments)} segments, "
            f"{len(speakers)} speakers"
        )

        # Upload to S3
        s3_bucket = os.environ.get("S3_BUCKET", "dalston-artifacts")
        transcript_uri = f"s3://{s3_bucket}/jobs/{job_id}/transcript.json"
        io.upload_json(transcript, transcript_uri)
        logger.info(f"Uploaded transcript to {transcript_uri}")

        return TaskOutput(data=transcript)

    def _find_speaker_by_overlap(
        self,
        seg_start: float,
        seg_end: float,
        diarization_segments: list[dict],
    ) -> str | None:
        """Find the speaker with maximum overlap for a transcript segment.

        Uses a simple overlap calculation: for each diarization segment that
        overlaps with the transcript segment, calculate the overlap duration.
        Return the speaker with the most total overlap.

        Args:
            seg_start: Transcript segment start time (seconds)
            seg_end: Transcript segment end time (seconds)
            diarization_segments: List of diarization segments with
                                  start, end, and speaker fields

        Returns:
            Speaker ID with maximum overlap, or None if no overlap found
        """
        if not diarization_segments:
            return None

        # Calculate overlap for each speaker
        speaker_overlaps: dict[str, float] = {}

        for diar_seg in diarization_segments:
            diar_start = diar_seg.get("start", 0.0)
            diar_end = diar_seg.get("end", 0.0)
            speaker = diar_seg.get("speaker")

            if not speaker:
                continue

            # Calculate overlap: max(0, min(end1, end2) - max(start1, start2))
            overlap_start = max(seg_start, diar_start)
            overlap_end = min(seg_end, diar_end)
            overlap = max(0.0, overlap_end - overlap_start)

            if overlap > 0:
                speaker_overlaps[speaker] = speaker_overlaps.get(speaker, 0.0) + overlap

        if not speaker_overlaps:
            return None

        # Return speaker with maximum overlap
        return max(speaker_overlaps, key=speaker_overlaps.get)


if __name__ == "__main__":
    engine = FinalMergerEngine()
    engine.run()
