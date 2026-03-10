"""Transcript assembly from stage outputs (M68).

Assembles MergeOutput from individual stage outputs without requiring
a merge engine. Called by the orchestrator on job completion for mono
pipelines. Per-channel pipelines still use a merge engine.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

from dalston.common.pipeline_types import (
    AlignOutput,
    DiarizeOutput,
    MergedSegment,
    MergeOutput,
    Segment,
    Speaker,
    SpeakerDetectionMode,
    SpeakerTurn,
    TranscribeOutput,
    TranscriptMetadata,
    Word,
)

logger = structlog.get_logger()


def assemble_transcript(
    *,
    job_id: str,
    stage_outputs: dict[str, Any],
    speaker_detection: str = "none",
    word_timestamps_requested: bool = False,
    known_speaker_names: list[str] | None = None,
    pipeline_stages: list[str] | None = None,
) -> MergeOutput:
    """Assemble a MergeOutput transcript from individual stage outputs.

    This replaces the merge engine for mono (non-per-channel) pipelines.
    It reads the outputs from transcribe, align, and diarize stages and
    builds the canonical transcript format.

    Args:
        job_id: The job identifier.
        stage_outputs: Dict mapping stage name to output data (raw dicts).
        speaker_detection: Speaker detection mode ("none", "diarize").
        word_timestamps_requested: Whether word timestamps were requested.
        known_speaker_names: Optional speaker name mapping.
        pipeline_stages: Explicit list of stages that ran. If None, inferred
            from stage_outputs keys.

    Returns:
        MergeOutput with assembled transcript.
    """
    # Parse stage outputs into typed models where possible
    prepare_data = stage_outputs.get("prepare", {})
    transcribe_data = stage_outputs.get("transcribe", {})
    align_data = stage_outputs.get("align")
    diarize_data = stage_outputs.get("diarize")

    # Extract audio metadata from prepare output
    audio_duration, audio_channels, sample_rate = _extract_audio_metadata(prepare_data)

    # Extract transcription data
    text, language, language_confidence, raw_segments = _extract_transcribe_data(
        transcribe_data,
    )

    # Parse typed outputs if possible
    transcribe_output = _try_parse_transcribe(transcribe_data)
    align_output = _try_parse_align(align_data) if align_data else None
    diarize_output = _try_parse_diarize(diarize_data) if diarize_data else None

    # Select segment source and determine word timestamp availability
    segments_source, word_timestamps_available, pipeline_warnings = _select_segments(
        transcribe_output=transcribe_output,
        align_output=align_output,
        raw_segments=raw_segments,
    )

    # Build speaker assignments from diarization
    diarization_turns, diarization_speakers = _extract_diarization(
        diarize_output=diarize_output,
        diarize_data=diarize_data,
        speaker_detection=speaker_detection,
        pipeline_warnings=pipeline_warnings,
    )

    # Build merged segments with IDs and speaker assignments
    segments = _build_merged_segments(
        segments_source=segments_source,
        diarization_turns=diarization_turns,
        word_timestamps_available=word_timestamps_available,
    )

    # Build speakers array
    speakers = _build_speakers(diarization_speakers)

    # Apply known speaker names if provided
    if isinstance(known_speaker_names, list) and known_speaker_names:
        _apply_known_speaker_names(segments, speakers, known_speaker_names)

    # Determine pipeline stages that ran
    if pipeline_stages is None:
        pipeline_stages = _infer_pipeline_stages(
            align_data=align_data,
            diarize_data=diarize_data,
            speaker_detection=speaker_detection,
        )

    # Build metadata
    now = datetime.now(UTC).isoformat()
    metadata = TranscriptMetadata(
        audio_duration=audio_duration,
        audio_channels=audio_channels,
        sample_rate=sample_rate,
        language=language,
        language_confidence=round(language_confidence, 3),
        word_timestamps=word_timestamps_available,
        word_timestamps_requested=word_timestamps_requested,
        speaker_detection=SpeakerDetectionMode(speaker_detection),
        speaker_count=len(speakers),
        created_at=now,
        completed_at=now,
        pipeline_stages=pipeline_stages,
        pipeline_warnings=pipeline_warnings,
    )

    transcript = MergeOutput(
        job_id=job_id,
        version="1.0",
        metadata=metadata,
        text=text,
        speakers=speakers,
        segments=segments,
        paragraphs=[],
        summary=None,
        redacted_text=None,
        pii_entities=None,
        pii_metadata=None,
    )

    logger.info(
        "transcript_assembled",
        job_id=job_id,
        segment_count=len(segments),
        char_count=len(text),
        language=language,
        word_timestamps=word_timestamps_available,
        speaker_count=len(speakers),
    )

    return transcript


def determine_terminal_stage(
    *,
    speaker_detection: str = "none",
    has_align: bool = False,
    has_diarize: bool = False,
    has_pii: bool = False,
    has_audio_redact: bool = False,
) -> str:
    """Determine the terminal stage name for a pipeline configuration.

    Returns the name of the last stage whose output is the final result.
    PII stages are post-processing and handled separately.

    Args:
        speaker_detection: Speaker detection mode.
        has_align: Whether alignment stage is included.
        has_diarize: Whether diarization stage is included.
        has_pii: Whether PII detection is included.
        has_audio_redact: Whether audio redaction is included.

    Returns:
        Stage name string.
    """
    if has_diarize and speaker_detection == "diarize":
        return "diarize"
    if has_align:
        return "align"
    return "transcribe"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_audio_metadata(
    prepare_data: dict[str, Any] | None,
) -> tuple[float, int, int]:
    """Extract audio duration, channels, and sample rate from prepare output."""
    if not prepare_data:
        return 0.0, 1, 16000

    # Try typed PrepareOutput format (channel_files array)
    channel_files = prepare_data.get("channel_files", [])
    if channel_files and isinstance(channel_files[0], dict):
        first = channel_files[0]
        return (
            first.get("duration", 0.0),
            first.get("channels", 1),
            first.get("sample_rate", 16000),
        )

    # Fallback to flat keys
    return (
        prepare_data.get("duration", 0.0),
        prepare_data.get("channels", 1),
        prepare_data.get("sample_rate", 16000),
    )


def _extract_transcribe_data(
    transcribe_data: dict[str, Any],
) -> tuple[str, str, float, list]:
    """Extract text, language, confidence, and raw segments from transcribe output."""
    text = transcribe_data.get("text", "")
    language = transcribe_data.get("language", "en")
    language_confidence_raw = transcribe_data.get("language_confidence")
    language_confidence = (
        language_confidence_raw if language_confidence_raw is not None else 1.0
    )
    raw_segments = transcribe_data.get("segments", [])
    return text, language, language_confidence, raw_segments


def _try_parse_transcribe(data: dict[str, Any]) -> TranscribeOutput | None:
    """Try to parse transcribe data into a typed TranscribeOutput."""
    try:
        return TranscribeOutput.model_validate(data)
    except Exception:
        return None


def _try_parse_align(data: dict[str, Any]) -> AlignOutput | None:
    """Try to parse align data into a typed AlignOutput."""
    try:
        return AlignOutput.model_validate(data)
    except Exception:
        return None


def _try_parse_diarize(data: dict[str, Any]) -> DiarizeOutput | None:
    """Try to parse diarize data into a typed DiarizeOutput."""
    try:
        return DiarizeOutput.model_validate(data)
    except Exception:
        return None


def _select_segments(
    *,
    transcribe_output: TranscribeOutput | None,
    align_output: AlignOutput | None,
    raw_segments: list,
) -> tuple[list[Segment] | list[dict], bool, list]:
    """Select the best available segments and determine word timestamp availability.

    Returns:
        Tuple of (segments_source, word_timestamps_available, pipeline_warnings).
    """
    pipeline_warnings: list = []

    if align_output is not None:
        if align_output.skipped:
            logger.warning("alignment_skipped", reason=align_output.skip_reason)
            pipeline_warnings.extend(align_output.warnings)
            segments_source = (
                transcribe_output.segments if transcribe_output else raw_segments
            )
            word_timestamps_available = False
        else:
            segments_source = align_output.segments
            word_timestamps_available = align_output.word_timestamps
    elif transcribe_output is not None:
        segments_source = transcribe_output.segments
        word_timestamps_available = any(s.words for s in segments_source)
    else:
        segments_source = raw_segments
        word_timestamps_available = False

    return segments_source, word_timestamps_available, pipeline_warnings


def _extract_diarization(
    *,
    diarize_output: DiarizeOutput | None,
    diarize_data: dict[str, Any] | None,
    speaker_detection: str,
    pipeline_warnings: list,
) -> tuple[list[SpeakerTurn], list[str]]:
    """Extract diarization turns and speakers from diarize output."""
    diarization_turns: list[SpeakerTurn] = []
    diarization_speakers: list[str] = []

    if speaker_detection != "diarize":
        return diarization_turns, diarization_speakers

    if diarize_output is not None:
        if diarize_output.skipped:
            pipeline_warnings.extend(diarize_output.warnings)
        else:
            diarization_turns = diarize_output.turns
            diarization_speakers = diarize_output.speakers
    elif diarize_data is not None:
        # Fall back to raw dict
        raw_turns = diarize_data.get("turns", [])
        for turn in raw_turns:
            if isinstance(turn, dict):
                try:
                    diarization_turns.append(SpeakerTurn.model_validate(turn))
                except Exception:
                    pass
        raw_speakers = diarize_data.get("speakers", [])
        if isinstance(raw_speakers, list):
            diarization_speakers = [s for s in raw_speakers if isinstance(s, str)]

    return diarization_turns, diarization_speakers


def _find_speaker_by_overlap(
    seg_start: float,
    seg_end: float,
    turns: list[SpeakerTurn],
) -> str | None:
    """Find the speaker with maximum overlap for a segment time range.

    This is the same overlap-matching logic previously in the merge engine.

    Complexity is O(segments * turns). For very long recordings with many
    diarization turns, an interval tree (e.g. ``intervaltree``) would
    reduce per-segment lookup to O(log n + k). Not needed at current
    workload sizes.
    """
    best_speaker = None
    best_overlap = 0.0

    for turn in turns:
        overlap_start = max(seg_start, turn.start)
        overlap_end = min(seg_end, turn.end)
        overlap = max(0.0, overlap_end - overlap_start)

        if overlap > best_overlap:
            best_overlap = overlap
            best_speaker = turn.speaker

    return best_speaker


def _normalize_words(words: list) -> list[Word]:
    """Normalize word data into typed Word objects."""
    result = []
    for w in words:
        if isinstance(w, Word):
            result.append(w)
        elif isinstance(w, dict):
            try:
                result.append(Word.model_validate(w))
            except Exception:
                pass
    return result or []


def _build_merged_segments(
    *,
    segments_source: list[Segment] | list[dict],
    diarization_turns: list[SpeakerTurn],
    word_timestamps_available: bool,
) -> list[MergedSegment]:
    """Build MergedSegment list with IDs and speaker assignments."""
    segments: list[MergedSegment] = []

    for idx, seg in enumerate(segments_source):
        if isinstance(seg, Segment):
            seg_start = seg.start
            seg_end = seg.end
            seg_text = seg.text
            seg_words = seg.words
            seg_tokens = seg.tokens
            seg_temperature = seg.temperature
            seg_avg_logprob = seg.avg_logprob
            seg_compression_ratio = seg.compression_ratio
            seg_no_speech_prob = seg.no_speech_prob
        elif hasattr(seg, "start"):
            # Pydantic model but not Segment (e.g. from align output)
            seg_start = seg.start
            seg_end = seg.end
            seg_text = seg.text
            seg_words = getattr(seg, "words", None)
            seg_tokens = getattr(seg, "tokens", None)
            seg_temperature = getattr(seg, "temperature", None)
            seg_avg_logprob = getattr(seg, "avg_logprob", None)
            seg_compression_ratio = getattr(seg, "compression_ratio", None)
            seg_no_speech_prob = getattr(seg, "no_speech_prob", None)
        else:
            seg_start = seg.get("start", 0.0)
            seg_end = seg.get("end", 0.0)
            seg_text = seg.get("text", "")
            seg_words = seg.get("words")
            seg_tokens = seg.get("tokens")
            seg_temperature = seg.get("temperature")
            seg_avg_logprob = seg.get("avg_logprob")
            seg_compression_ratio = seg.get("compression_ratio")
            seg_no_speech_prob = seg.get("no_speech_prob")

        # Assign speaker based on diarization overlap
        speaker = None
        if diarization_turns:
            speaker = _find_speaker_by_overlap(seg_start, seg_end, diarization_turns)

        # Normalize words
        words: list[Word] | None = None
        if word_timestamps_available and seg_words:
            words = _normalize_words(seg_words)

        segment = MergedSegment(
            id=f"seg_{idx:03d}",
            start=seg_start,
            end=seg_end,
            text=seg_text,
            speaker=speaker,
            words=words,
            tokens=seg_tokens if isinstance(seg_tokens, list) else None,
            temperature=seg_temperature,
            avg_logprob=seg_avg_logprob,
            compression_ratio=seg_compression_ratio,
            no_speech_prob=seg_no_speech_prob,
            emotion=None,
            emotion_confidence=None,
            events=[],
        )
        segments.append(segment)

    return segments


def _build_speakers(diarization_speakers: list[str]) -> list[Speaker]:
    """Build Speaker list from diarization speaker IDs."""
    return [Speaker(id=sid, label=None) for sid in diarization_speakers]


def _apply_known_speaker_names(
    segments: list[MergedSegment],
    speakers: list[Speaker],
    known_speaker_names: list[str],
) -> dict[str, str]:
    """Apply known speaker names to speakers.

    Sets the label on each Speaker object. Segment speaker fields keep the
    original speaker ID; consumers resolve labels via the speakers array.

    Returns:
        Mapping of original speaker ID to new label.
    """
    remapped: dict[str, str] = {}

    for idx, speaker in enumerate(speakers):
        if idx < len(known_speaker_names):
            remapped[speaker.id] = known_speaker_names[idx]
            speaker.label = known_speaker_names[idx]

    return remapped


def _infer_pipeline_stages(
    *,
    align_data: dict[str, Any] | None,
    diarize_data: dict[str, Any] | None,
    speaker_detection: str,
) -> list[str]:
    """Infer pipeline stages from available outputs."""
    stages = ["prepare", "transcribe"]
    if align_data is not None:
        stages.append("align")
    if diarize_data is not None and speaker_detection == "diarize":
        stages.append("diarize")
    return stages
