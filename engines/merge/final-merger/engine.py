"""Final merger engine for combining pipeline outputs.

Combines outputs from prepare, transcribe, align, and diarize stages
into the standard Dalston transcript format with segment IDs and metadata.

For per_channel mode with PII and audio redaction, also combines:
- PII entities from each channel
- Redacted mono WAVs into stereo output (using FFmpeg)
"""

import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from dalston.engine_sdk import (
    Engine,
    MergedSegment,
    MergeOutput,
    PIIEntity,
    PIIMetadata,
    Speaker,
    SpeakerDetectionMode,
    SpeakerTurn,
    TaskInput,
    TaskOutput,
    TranscriptMetadata,
    Word,
    io,
)


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
            TaskOutput with MergeOutput containing the final transcript
        """
        job_id = input.job_id
        config = input.config

        # Get speaker detection mode from config
        speaker_detection_str = config.get("speaker_detection", "none")
        speaker_detection = SpeakerDetectionMode(speaker_detection_str)

        self.logger.info("merging_outputs", job_id=str(job_id))

        # Handle per_channel mode separately
        if speaker_detection == SpeakerDetectionMode.PER_CHANNEL:
            return self._merge_per_channel(input, config)

        # Get typed outputs from upstream stages
        prepare_output = input.get_prepare_output()
        transcribe_output = input.get_transcribe_output()
        align_output = input.get_align_output()
        diarize_output = input.get_diarize_output()

        # Fall back to raw dict if typed parsing fails
        if not prepare_output:
            raw_prepare = input.get_raw_output("prepare") or {}
            audio_duration = raw_prepare.get("duration", 0.0)
            audio_channels = raw_prepare.get("channels", 1)
            sample_rate = raw_prepare.get("sample_rate", 16000)
            if not raw_prepare:
                self.logger.warning(
                    "using_default_audio_metadata",
                    reason="No prepare output available",
                    defaults={"duration": 0.0, "channels": 1, "sample_rate": 16000},
                )
        else:
            # Get audio metadata from the first channel file
            if prepare_output.channel_files:
                first_channel = prepare_output.channel_files[0]
                audio_duration = first_channel.duration
                audio_channels = first_channel.channels
                sample_rate = first_channel.sample_rate
            else:
                # Fallback if no channel files
                audio_duration = 0.0
                audio_channels = 1
                sample_rate = 16000
                self.logger.warning(
                    "using_default_audio_metadata",
                    reason="Prepare output has no channel files",
                    defaults={"duration": 0.0, "channels": 1, "sample_rate": 16000},
                )

        if not transcribe_output:
            raw_transcribe = input.get_raw_output("transcribe") or {}
            text = raw_transcribe.get("text", "")
            language = raw_transcribe.get("language", "en")
            language_confidence = raw_transcribe.get("language_confidence", 1.0)
            raw_segments = raw_transcribe.get("segments", [])
        else:
            text = transcribe_output.text
            language = transcribe_output.language
            language_confidence = transcribe_output.language_confidence or 1.0
            raw_segments = None  # Will use typed segments

        # Determine which output to use for segments
        pipeline_warnings: list = []
        word_timestamps_requested = config.get("word_timestamps", False)

        if align_output:
            if align_output.skipped:
                self.logger.warning(
                    "alignment_skipped", reason=align_output.skip_reason
                )
                pipeline_warnings.extend(align_output.warnings)
                # Use transcribe segments as fallback
                segments_source = (
                    transcribe_output.segments if transcribe_output else []
                )
                word_timestamps_available = False
            else:
                segments_source = align_output.segments
                word_timestamps_available = align_output.word_timestamps
                self.logger.info("using_aligned_segments")
        elif transcribe_output:
            segments_source = transcribe_output.segments
            word_timestamps_available = any(s.words for s in segments_source)
        else:
            # Fall back to raw segments
            segments_source = raw_segments or []
            word_timestamps_available = False

        # Extract diarization data if available
        diarization_turns: list[SpeakerTurn] = []
        diarization_speakers: list[str] = []

        if diarize_output and speaker_detection == SpeakerDetectionMode.DIARIZE:
            if diarize_output.skipped:
                skip_reason = diarize_output.skip_reason or "Unknown reason"
                self.logger.warning("diarization_skipped", reason=skip_reason)
                pipeline_warnings.extend(diarize_output.warnings)
            else:
                diarization_turns = diarize_output.turns
                diarization_speakers = diarize_output.speakers
                self.logger.info(
                    "using_diarization",
                    speaker_count=len(diarization_speakers),
                    segment_count=len(diarization_turns),
                )

        # Build segments with IDs and speaker assignments
        segments: list[MergedSegment] = []
        for idx, seg in enumerate(segments_source):
            # Handle both typed Segment and raw dict
            if hasattr(seg, "start"):
                seg_start = seg.start
                seg_end = seg.end
                seg_text = seg.text
                seg_words = seg.words
            else:
                seg_start = seg.get("start", 0.0)
                seg_end = seg.get("end", 0.0)
                seg_text = seg.get("text", "")
                seg_words = seg.get("words")

            # Assign speaker based on diarization overlap
            speaker = None
            if diarization_turns:
                speaker = self._find_speaker_by_overlap(
                    seg_start, seg_end, diarization_turns
                )

            # Normalize words
            words: list[Word] | None = None
            if word_timestamps_available and seg_words:
                words = self._normalize_words(seg_words)

            segment = MergedSegment(
                id=f"seg_{idx:03d}",
                start=seg_start,
                end=seg_end,
                text=seg_text,
                speaker=speaker,
                words=words,
                emotion=None,
                emotion_confidence=None,
                events=[],
            )
            segments.append(segment)

        # Build speakers array
        speakers: list[Speaker] = []
        if diarization_speakers:
            for speaker_id in diarization_speakers:
                speakers.append(Speaker(id=speaker_id, label=None))
            self.logger.info("built_speakers_array", speaker_count=len(speakers))

        # Determine pipeline stages that ran
        pipeline_stages = ["prepare", "transcribe"]
        if align_output:
            pipeline_stages.append("align")
        if diarize_output and speaker_detection == SpeakerDetectionMode.DIARIZE:
            pipeline_stages.append("diarize")

        # PII detection integration (M26)
        pii_detection_enabled = config.get("pii_detection", False)
        redacted_text = None
        pii_entities: list[PIIEntity] | None = None
        pii_metadata: PIIMetadata | None = None

        if pii_detection_enabled:
            pii_detect_output = input.get_pii_detect_output()
            audio_redact_output = input.get_audio_redact_output()

            if pii_detect_output:
                pipeline_stages.append("pii_detect")
                redacted_text = pii_detect_output.redacted_text
                pii_entities = pii_detect_output.entities

                # Build PII metadata
                redacted_audio_uri = None
                if audio_redact_output:
                    pipeline_stages.append("audio_redact")
                    redacted_audio_uri = audio_redact_output.redacted_audio_uri

                pii_metadata = PIIMetadata(
                    detection_tier=pii_detect_output.detection_tier,
                    entities_detected=len(pii_detect_output.entities),
                    entity_count_by_type=pii_detect_output.entity_count_by_type,
                    entity_count_by_category=pii_detect_output.entity_count_by_category,
                    redacted_audio_uri=redacted_audio_uri,
                    processing_time_ms=pii_detect_output.processing_time_ms,
                )

                self.logger.info(
                    "pii_detection_integrated",
                    entities_detected=len(pii_entities),
                    redacted_audio=redacted_audio_uri is not None,
                )
            else:
                # Try raw output
                raw_pii = input.get_raw_output("pii_detect")
                if raw_pii:
                    pipeline_stages.append("pii_detect")
                    redacted_text = raw_pii.get("redacted_text")

        pipeline_stages.append("merge")

        # Build metadata
        metadata = TranscriptMetadata(
            audio_duration=audio_duration,
            audio_channels=audio_channels,
            sample_rate=sample_rate,
            language=language,
            language_confidence=round(language_confidence, 3),
            word_timestamps=word_timestamps_available,
            word_timestamps_requested=word_timestamps_requested,
            speaker_detection=speaker_detection,
            speaker_count=len(speakers),
            created_at=datetime.now(UTC).isoformat(),
            completed_at=datetime.now(UTC).isoformat(),
            pipeline_stages=pipeline_stages,
            pipeline_warnings=pipeline_warnings,
        )

        # Build the final transcript structure
        transcript = MergeOutput(
            job_id=str(job_id),
            version="1.0",
            metadata=metadata,
            text=text,
            speakers=speakers,
            segments=segments,
            paragraphs=[],
            summary=None,
            redacted_text=redacted_text,
            pii_entities=pii_entities,
            pii_metadata=pii_metadata,
        )

        self.logger.info(
            "merged_transcript",
            segment_count=len(segments),
            char_count=len(text),
            language=language,
            word_timestamps=word_timestamps_available,
            speaker_count=len(speakers),
        )

        # Write to the canonical transcript location for the Gateway
        s3_bucket = os.environ.get("S3_BUCKET", "dalston-artifacts")
        transcript_uri = f"s3://{s3_bucket}/jobs/{job_id}/transcript.json"
        io.upload_json(transcript.model_dump(mode="json"), transcript_uri)
        self.logger.info("uploaded_transcript", transcript_uri=transcript_uri)

        return TaskOutput(data=transcript)

    def _merge_per_channel(
        self,
        input: TaskInput,
        config: dict,
    ) -> TaskOutput:
        """Merge transcripts from per-channel processing.

        Interleaves segments from multiple channel transcripts by timestamp,
        assigning speakers based on channel (SPEAKER_00 for ch0, etc.).

        For per-channel PII and audio redaction:
        - Collects PII entities from each channel
        - Assembles redacted mono WAVs into stereo output using FFmpeg

        Args:
            input: Task input with previous_outputs containing channel data
            config: Merge task config

        Returns:
            TaskOutput with MergeOutput containing merged transcript
        """
        job_id = input.job_id
        word_timestamps = config.get("word_timestamps", False)
        pii_detection_enabled = config.get("pii_detection", False)
        redact_pii_audio = config.get("redact_pii_audio", False)

        # Get prepare output
        prepare_output = input.get_prepare_output()
        if prepare_output:
            # Get audio metadata from the first channel file
            channel_files = prepare_output.channel_files or []
            if channel_files:
                first_channel = channel_files[0]
                audio_duration = first_channel.duration
                audio_channels = len(
                    channel_files
                )  # Number of channels = number of files
                sample_rate = first_channel.sample_rate
            else:
                audio_duration = 0.0
                audio_channels = 2
                sample_rate = 16000
                self.logger.warning(
                    "using_default_audio_metadata",
                    reason="Prepare output has no channel files (per-channel mode)",
                    defaults={"duration": 0.0, "channels": 2, "sample_rate": 16000},
                )
            channel_count = config.get("channel_count") or len(channel_files) or 2
        else:
            raw_prepare = input.get_raw_output("prepare") or {}
            audio_duration = raw_prepare.get("duration", 0.0)
            audio_channels = raw_prepare.get("original_channels", 2)
            sample_rate = raw_prepare.get("sample_rate", 16000)
            channel_files = raw_prepare.get("channel_files", [])
            channel_count = config.get("channel_count") or len(channel_files) or 2
            if not raw_prepare:
                self.logger.warning(
                    "using_default_audio_metadata",
                    reason="No prepare output available (per-channel mode)",
                    defaults={"duration": 0.0, "channels": 2, "sample_rate": 16000},
                )

        self.logger.info("merging_per_channel_outputs", channel_count=channel_count)

        # Collect segments and PII data from all channels
        all_segments: list[dict] = []
        all_pii_entities: list[PIIEntity] = []
        redacted_audio_uris: list[str] = []
        pipeline_warnings: list = []
        language = "en"
        language_confidence = 1.0

        # Track entity counts across channels
        total_entity_count_by_type: dict[str, int] = {}
        total_entity_count_by_category: dict[str, int] = {}
        pii_detection_tier: str | None = None
        total_pii_processing_time_ms = 0

        for channel in range(channel_count):
            transcribe_key = f"transcribe_ch{channel}"
            align_key = f"align_ch{channel}"
            pii_detect_key = f"pii_detect_ch{channel}"
            audio_redact_key = f"audio_redact_ch{channel}"

            transcribe_output = input.get_transcribe_output(transcribe_key)
            align_output = input.get_align_output(align_key)

            if not transcribe_output and not align_output:
                # Try raw access
                raw_transcribe = input.get_raw_output(transcribe_key)
                raw_align = input.get_raw_output(align_key)
                if not raw_transcribe and not raw_align:
                    self.logger.warning("missing_channel_output", channel=channel)
                    continue

            # Use first channel's language detection
            if channel == 0 and transcribe_output:
                language = transcribe_output.language
                language_confidence = transcribe_output.language_confidence or 1.0

            # Get segments from align or transcribe
            if align_output and not align_output.skipped:
                raw_segments = align_output.segments
                has_words = align_output.word_timestamps
            elif transcribe_output:
                raw_segments = transcribe_output.segments
                has_words = any(s.words for s in raw_segments)
            else:
                raw_transcribe = input.get_raw_output(transcribe_key) or {}
                raw_segments = raw_transcribe.get("segments", [])
                has_words = False

            if align_output and align_output.skipped:
                pipeline_warnings.extend(align_output.warnings)

            # Get source channel from transcribe output, with fallback to loop index
            source_channel = channel  # Default to loop index
            if transcribe_output and transcribe_output.channel is not None:
                source_channel = transcribe_output.channel

            # Add channel/speaker info to each segment
            speaker_id = f"SPEAKER_{source_channel:02d}"
            for seg in raw_segments:
                if hasattr(seg, "start"):
                    all_segments.append(
                        {
                            "start": seg.start,
                            "end": seg.end,
                            "text": seg.text,
                            "speaker": speaker_id,
                            "words": seg.words if has_words else None,
                            "channel": source_channel,
                        }
                    )
                else:
                    all_segments.append(
                        {
                            "start": seg.get("start", 0.0),
                            "end": seg.get("end", 0.0),
                            "text": seg.get("text", ""),
                            "speaker": speaker_id,
                            "words": seg.get("words") if has_words else None,
                            "channel": source_channel,
                        }
                    )

            # Collect PII detection output for this channel
            if pii_detection_enabled:
                pii_detect_output = input.get_pii_detect_output(pii_detect_key)
                if pii_detect_output:
                    # Add channel info via speaker field
                    for entity in pii_detect_output.entities:
                        # Create a copy with channel info in speaker field
                        speaker_with_channel = (
                            f"{entity.speaker or ''}_ch{channel}"
                            if entity.speaker
                            else f"ch{channel}"
                        )
                        entity_with_channel = PIIEntity(
                            entity_type=entity.entity_type,
                            category=entity.category,
                            start_offset=entity.start_offset,
                            end_offset=entity.end_offset,
                            start_time=entity.start_time,
                            end_time=entity.end_time,
                            confidence=entity.confidence,
                            speaker=speaker_with_channel,
                            redacted_value=entity.redacted_value,
                            original_text=entity.original_text,
                        )
                        all_pii_entities.append(entity_with_channel)

                    # Aggregate entity counts
                    pii_detection_tier = pii_detect_output.detection_tier
                    total_pii_processing_time_ms += (
                        pii_detect_output.processing_time_ms or 0
                    )

                    for etype, count in (
                        pii_detect_output.entity_count_by_type or {}
                    ).items():
                        total_entity_count_by_type[etype] = (
                            total_entity_count_by_type.get(etype, 0) + count
                        )

                    for cat, count in (
                        pii_detect_output.entity_count_by_category or {}
                    ).items():
                        total_entity_count_by_category[cat] = (
                            total_entity_count_by_category.get(cat, 0) + count
                        )

                    self.logger.info(
                        "collected_pii_from_channel",
                        channel=channel,
                        entity_count=len(pii_detect_output.entities),
                    )

            # Collect audio redaction output for this channel
            if redact_pii_audio:
                audio_redact_output = input.get_audio_redact_output(audio_redact_key)
                if audio_redact_output and audio_redact_output.redacted_audio_uri:
                    redacted_audio_uris.append(audio_redact_output.redacted_audio_uri)
                    self.logger.info(
                        "collected_redacted_audio_from_channel",
                        channel=channel,
                        uri=audio_redact_output.redacted_audio_uri,
                    )

        # Sort all segments by start time (interleave)
        all_segments.sort(key=lambda s: s["start"])

        # Build final segments with IDs and per-segment redacted text
        segments: list[MergedSegment] = []
        redacted_segment_texts: list[str] = []

        for idx, seg in enumerate(all_segments):
            words = self._normalize_words(seg["words"]) if seg.get("words") else None

            # Compute per-segment redacted text if PII was detected
            redacted_seg_text: str | None = None
            if pii_detection_enabled and all_pii_entities:
                seg_channel = seg.get("channel", 0)
                redacted_seg_text = self._redact_segment_text(
                    segment_text=seg["text"],
                    segment_start=seg["start"],
                    segment_end=seg["end"],
                    segment_channel=seg_channel,
                    pii_entities=all_pii_entities,
                    words=seg.get("words"),
                )
                redacted_segment_texts.append(redacted_seg_text)

            segment = MergedSegment(
                id=f"seg_{idx:03d}",
                start=seg["start"],
                end=seg["end"],
                text=seg["text"],
                redacted_text=redacted_seg_text,
                speaker=seg["speaker"],
                words=words,
                emotion=None,
                emotion_confidence=None,
                events=[],
            )
            segments.append(segment)

        # Build speakers array
        speakers = [
            Speaker(id=f"SPEAKER_{ch:02d}", label=None, channel=ch)
            for ch in range(channel_count)
        ]

        # Combine text from all segments
        text = " ".join(seg.text for seg in segments if seg.text)

        # Build full redacted text from per-segment redacted texts
        redacted_text: str | None = None
        if redacted_segment_texts:
            redacted_text = " ".join(redacted_segment_texts)

        # Determine pipeline stages
        pipeline_stages = ["prepare"]
        for ch in range(channel_count):
            pipeline_stages.append(f"transcribe_ch{ch}")
            if word_timestamps:
                pipeline_stages.append(f"align_ch{ch}")
            if pii_detection_enabled:
                pipeline_stages.append(f"pii_detect_ch{ch}")
            if redact_pii_audio:
                pipeline_stages.append(f"audio_redact_ch{ch}")
        pipeline_stages.append("merge")

        # Assemble redacted stereo audio if we have redacted mono files
        redacted_stereo_uri: str | None = None
        if redact_pii_audio and len(redacted_audio_uris) == channel_count:
            redacted_stereo_uri = self._assemble_stereo_audio(
                job_id=str(job_id),
                channel_uris=redacted_audio_uris,
                sample_rate=sample_rate,
            )
            if redacted_stereo_uri:
                self.logger.info(
                    "assembled_stereo_audio", stereo_uri=redacted_stereo_uri
                )

        # Build PII metadata if detection was enabled
        pii_metadata: PIIMetadata | None = None
        if pii_detection_enabled and (all_pii_entities or pii_detection_tier):
            pii_metadata = PIIMetadata(
                detection_tier=pii_detection_tier or "standard",
                entities_detected=len(all_pii_entities),
                entity_count_by_type=total_entity_count_by_type or None,
                entity_count_by_category=total_entity_count_by_category or None,
                redacted_audio_uri=redacted_stereo_uri,
                processing_time_ms=total_pii_processing_time_ms or None,
            )

        # Build metadata
        metadata = TranscriptMetadata(
            audio_duration=audio_duration,
            audio_channels=audio_channels,
            sample_rate=sample_rate,
            language=language,
            language_confidence=round(language_confidence, 3),
            word_timestamps=word_timestamps,
            word_timestamps_requested=word_timestamps,
            speaker_detection=SpeakerDetectionMode.PER_CHANNEL,
            speaker_count=len(speakers),
            created_at=datetime.now(UTC).isoformat(),
            completed_at=datetime.now(UTC).isoformat(),
            pipeline_stages=pipeline_stages,
            pipeline_warnings=pipeline_warnings,
        )

        # Build transcript
        transcript = MergeOutput(
            job_id=str(job_id),
            version="1.0",
            metadata=metadata,
            text=text,
            speakers=speakers,
            segments=segments,
            paragraphs=[],
            summary=None,
            redacted_text=redacted_text,
            pii_entities=all_pii_entities if all_pii_entities else None,
            pii_metadata=pii_metadata,
        )

        self.logger.info(
            "merged_per_channel_transcript",
            segment_count=len(segments),
            speaker_count=len(speakers),
            pii_entities_count=len(all_pii_entities) if all_pii_entities else 0,
            redacted_stereo_audio=redacted_stereo_uri is not None,
        )

        # Upload to S3
        s3_bucket = os.environ.get("S3_BUCKET", "dalston-artifacts")
        transcript_uri = f"s3://{s3_bucket}/jobs/{job_id}/transcript.json"
        io.upload_json(transcript.model_dump(mode="json"), transcript_uri)
        self.logger.info("uploaded_transcript", transcript_uri=transcript_uri)

        return TaskOutput(data=transcript)

    def _assemble_stereo_audio(
        self,
        job_id: str,
        channel_uris: list[str],
        sample_rate: int = 16000,
    ) -> str | None:
        """Assemble multiple mono WAV files into a stereo WAV file using FFmpeg.

        Downloads channel audio files from S3, uses FFmpeg to merge them into
        stereo, and uploads the result back to S3.

        Args:
            job_id: Job ID for output path
            channel_uris: List of S3 URIs for mono channel files (ch0, ch1, ...)
            sample_rate: Target sample rate

        Returns:
            S3 URI of the assembled stereo file, or None on failure
        """
        if len(channel_uris) < 2:
            self.logger.warning(
                "insufficient_channels_for_stereo",
                channel_count=len(channel_uris),
            )
            return None

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)

            # Download channel files
            local_channels: list[Path] = []
            for idx, uri in enumerate(channel_uris):
                local_path = tmppath / f"channel_{idx}.wav"
                try:
                    io.download_file(uri, local_path)
                    local_channels.append(local_path)
                    self.logger.info(
                        "downloaded_channel_audio",
                        channel=idx,
                        local_path=str(local_path),
                    )
                except Exception as e:
                    self.logger.error(
                        "failed_to_download_channel", channel=idx, uri=uri, error=str(e)
                    )
                    return None

            # Build FFmpeg command to merge mono files into stereo
            # Using amerge filter to combine channels
            output_path = tmppath / "redacted_stereo.wav"

            # For 2 channels: merge left and right
            # FFmpeg command: ffmpeg -i ch0.wav -i ch1.wav -filter_complex amerge=inputs=2 -ac 2 output.wav
            cmd = ["ffmpeg", "-y"]
            for ch_path in local_channels:
                cmd.extend(["-i", str(ch_path)])

            cmd.extend(
                [
                    "-filter_complex",
                    f"amerge=inputs={len(local_channels)}",
                    "-ac",
                    str(len(local_channels)),
                    "-ar",
                    str(sample_rate),
                    str(output_path),
                ]
            )

            self.logger.info("running_ffmpeg_stereo_assembly", command=" ".join(cmd))

            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if result.returncode != 0:
                    self.logger.error(
                        "ffmpeg_stereo_assembly_failed",
                        returncode=result.returncode,
                        stderr=result.stderr[:500] if result.stderr else None,
                    )
                    return None
            except FileNotFoundError:
                self.logger.error(
                    "ffmpeg_not_found",
                    hint="FFmpeg is required for stereo assembly",
                )
                return None
            except subprocess.TimeoutExpired:
                self.logger.error("ffmpeg_stereo_assembly_timeout")
                return None
            except Exception as e:
                self.logger.error("ffmpeg_stereo_assembly_error", error=str(e))
                return None

            # Upload stereo file to S3
            s3_bucket = os.environ.get("S3_BUCKET", "dalston-artifacts")
            stereo_uri = f"s3://{s3_bucket}/jobs/{job_id}/redacted_stereo.wav"

            try:
                io.upload_file(str(output_path), stereo_uri)
                self.logger.info("uploaded_stereo_audio", uri=stereo_uri)
                return stereo_uri
            except Exception as e:
                self.logger.error("failed_to_upload_stereo", error=str(e))
                return None

    def _redact_segment_text(
        self,
        segment_text: str,
        segment_start: float,
        segment_end: float,
        segment_channel: int,
        pii_entities: list[PIIEntity],
        words: list | None = None,
    ) -> str:
        """Redact PII from segment text based on timing overlap.

        For per-channel mode, we can't use character offsets because they're
        relative to each channel's original text. Instead, we match entities
        by time overlap and use word-level replacement if words are available.

        Args:
            segment_text: Original segment text
            segment_start: Segment start time
            segment_end: Segment end time
            segment_channel: Channel index (0, 1, etc.)
            pii_entities: All PII entities with timing info
            words: Word-level timing info if available

        Returns:
            Redacted segment text
        """
        # Find entities that overlap with this segment's time range and channel
        channel_suffix = f"ch{segment_channel}"
        overlapping_entities = []
        for entity in pii_entities:
            # Check if entity is from this channel (speaker ends with ch0, ch1, etc.)
            if entity.speaker and entity.speaker.endswith(channel_suffix):
                # Check time overlap
                if entity.start_time < segment_end and entity.end_time > segment_start:
                    overlapping_entities.append(entity)

        if not overlapping_entities:
            return segment_text

        # If we have words, do word-level replacement
        if words:
            redacted_words = []
            for w in words:
                word_text = w.get("text", "") if isinstance(w, dict) else w.text
                word_start = w.get("start", 0) if isinstance(w, dict) else w.start
                word_end = w.get("end", 0) if isinstance(w, dict) else w.end

                # Check if any entity overlaps with this word
                redacted = False
                for entity in overlapping_entities:
                    if entity.start_time <= word_start and entity.end_time >= word_end:
                        # Word is fully within entity - redact it
                        redacted_words.append(f"[{entity.entity_type.upper()}]")
                        redacted = True
                        break
                    elif word_start < entity.end_time and word_end > entity.start_time:
                        # Partial overlap - also redact
                        redacted_words.append(f"[{entity.entity_type.upper()}]")
                        redacted = True
                        break

                if not redacted:
                    redacted_words.append(word_text)

            # Deduplicate consecutive redaction markers
            result_words = []
            prev_marker = None
            for word in redacted_words:
                if word.startswith("[") and word.endswith("]"):
                    if word != prev_marker:
                        result_words.append(word)
                        prev_marker = word
                else:
                    result_words.append(word)
                    prev_marker = None

            return " ".join(result_words)

        # No words - do simple text replacement using original_text
        redacted = segment_text
        # Sort by length (longest first) to avoid partial replacements
        sorted_entities = sorted(
            overlapping_entities, key=lambda e: len(e.original_text), reverse=True
        )
        for entity in sorted_entities:
            if entity.original_text in redacted:
                redacted = redacted.replace(
                    entity.original_text, f"[{entity.entity_type.upper()}]", 1
                )

        return redacted

    def _normalize_words(self, words: list) -> list[Word]:
        """Normalize word structures per pipeline interface spec.

        Args:
            words: List of Word objects or word dicts from transcription/alignment

        Returns:
            List of normalized Word objects
        """
        normalized: list[Word] = []
        for w in words:
            if hasattr(w, "text"):
                # Already a Word object
                normalized.append(w)
            else:
                # Raw dict
                normalized.append(
                    Word(
                        text=w.get("text", ""),
                        start=w.get("start", 0.0),
                        end=w.get("end", 0.0),
                        confidence=w.get("confidence"),
                        alignment_method=w.get("alignment_method"),
                    )
                )
        return normalized

    def _find_speaker_by_overlap(
        self,
        seg_start: float,
        seg_end: float,
        speaker_turns: list[SpeakerTurn],
    ) -> str | None:
        """Find the speaker with maximum overlap for a transcript segment.

        Uses a simple overlap calculation: for each speaker turn that
        overlaps with the transcript segment, calculate the overlap duration.
        Return the speaker with the most total overlap.

        Args:
            seg_start: Transcript segment start time (seconds)
            seg_end: Transcript segment end time (seconds)
            speaker_turns: List of SpeakerTurn objects

        Returns:
            Speaker ID with maximum overlap, or None if no overlap found
        """
        if not speaker_turns:
            return None

        # Calculate overlap for each speaker
        speaker_overlaps: dict[str, float] = {}

        for turn in speaker_turns:
            # Calculate overlap: max(0, min(end1, end2) - max(start1, start2))
            overlap_start = max(seg_start, turn.start)
            overlap_end = min(seg_end, turn.end)
            overlap = max(0.0, overlap_end - overlap_start)

            if overlap > 0:
                speaker_overlaps[turn.speaker] = (
                    speaker_overlaps.get(turn.speaker, 0.0) + overlap
                )

        if not speaker_overlaps:
            return None

        # Return speaker with maximum overlap
        return max(speaker_overlaps, key=lambda k: speaker_overlaps[k])


if __name__ == "__main__":
    engine = FinalMergerEngine()
    engine.run()
