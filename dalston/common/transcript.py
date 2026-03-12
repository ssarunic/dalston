"""Transcript assembly from stage outputs (M68).

Assembles MergeOutput from individual stage outputs without requiring
a merge engine. Called by the orchestrator on job completion for both
mono and per-channel pipelines.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog

from dalston.common.pipeline_types import (
    AlignOutput,
    DiarizeOutput,
    LanguageInfo,
    MergedSegment,
    MergeOutput,
    Segment,
    Speaker,
    SpeakerDetectionMode,
    SpeakerTurn,
    Transcript,
    TranscriptMetadata,
    TranscriptSegment,
    Word,
)

logger = structlog.get_logger()

# Diarization turns shorter than this are merged into neighbours.
# Eliminates micro-turn chatter that pyannote produces during speaker overlap,
# which otherwise creates single-word segments at speaker boundaries.
MIN_TURN_DURATION_S = 0.25

# After word-level speaker splitting, segments with fewer words than this
# are merged into the nearest neighbour.  Prevents orphan single-word segments
# that arise from genuine but very brief overlapping speech.
MIN_SEGMENT_WORDS = 2


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
    text, language, language_confidence, languages = _extract_transcribe_data(
        transcribe_data
    )

    # Parse typed outputs — transcribe must be a valid Transcript
    if not transcribe_data:
        raise ValueError("Missing 'transcribe' stage output")
    transcript_v1 = _parse_transcript(transcribe_data)
    align_output = _try_parse_align(align_data) if align_data else None
    diarize_output = _try_parse_diarize(diarize_data) if diarize_data else None

    # Select segment source and determine word timestamp availability
    segments_source, word_timestamps_available, pipeline_warnings = _select_segments(
        align_output=align_output,
        transcript=transcript_v1,
    )

    # Build speaker assignments from diarization
    diarization_turns, diarization_speakers = _extract_diarization(
        diarize_output=diarize_output,
        diarize_data=diarize_data,
        speaker_detection=speaker_detection,
        pipeline_warnings=pipeline_warnings,
    )

    # Smooth out micro-turns from overlap chatter before word assignment.
    if diarization_turns:
        raw_count = len(diarization_turns)
        diarization_turns = _smooth_diarization_turns(diarization_turns)
        if len(diarization_turns) != raw_count:
            logger.info(
                "diarization_turns_smoothed",
                raw_turns=raw_count,
                smoothed_turns=len(diarization_turns),
                min_duration_s=MIN_TURN_DURATION_S,
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
        languages=languages,
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
        languages_detected=len(languages) if languages else 1,
        word_timestamps=word_timestamps_available,
        speaker_count=len(speakers),
    )

    return transcript


def assemble_per_channel_transcript(
    *,
    job_id: str,
    stage_outputs: dict[str, Any],
    channel_count: int = 2,
    word_timestamps_requested: bool = False,
    known_speaker_names: list[str] | None = None,
    pipeline_stages: list[str] | None = None,
) -> MergeOutput:
    """Assemble a MergeOutput transcript from per-channel stage outputs.

    This replaces the merge engine for per-channel pipelines. Each audio
    channel is treated as a separate speaker. Segments from all channels
    are interleaved by start time.

    Stage outputs are expected to have channel-suffixed keys:
    ``transcribe_ch0``, ``transcribe_ch1``, ``align_ch0``, ``align_ch1``, etc.

    Args:
        job_id: The job identifier.
        stage_outputs: Dict mapping stage name to output data (raw dicts).
            Expected keys: ``prepare``, ``transcribe_ch0``, ``transcribe_ch1``,
            and optionally ``align_ch0``, ``align_ch1``, etc.
        channel_count: Number of audio channels.
        word_timestamps_requested: Whether word timestamps were requested.
        known_speaker_names: Optional speaker name mapping.
        pipeline_stages: Explicit list of stages that ran.

    Returns:
        MergeOutput with assembled transcript.
    """
    prepare_data = stage_outputs.get("prepare", {})
    audio_duration, audio_channels, sample_rate = _extract_audio_metadata(prepare_data)

    # Collect segments from each channel, annotated with speaker
    all_channel_segments: list[dict[str, Any]] = []
    word_timestamps_available = False
    pipeline_warnings: list[str] = []
    language = "en"
    language_confidence = 1.0
    all_languages: dict[str, LanguageInfo] = {}  # keyed by code, merge across channels

    for channel in range(channel_count):
        transcribe_key = f"transcribe_ch{channel}"
        align_key = f"align_ch{channel}"
        speaker_id = f"SPEAKER_{channel:02d}"

        transcribe_data = stage_outputs.get(transcribe_key, {})
        align_data = stage_outputs.get(align_key)

        # Use first channel's language info as primary
        if channel == 0 and transcribe_data:
            language = transcribe_data.get("language", "en")
            lc_raw = transcribe_data.get("language_confidence")
            language_confidence = lc_raw if lc_raw is not None else 1.0

        # Collect per-channel language lists for code-switching metadata
        if transcribe_data:
            _, _, _, ch_languages = _extract_transcribe_data(transcribe_data)
            if ch_languages:
                for lang_info in ch_languages:
                    existing = all_languages.get(lang_info.code)
                    if existing is None or lang_info.confidence > existing.confidence:
                        all_languages[lang_info.code] = lang_info

        # Parse typed outputs — transcribe must be a valid Transcript
        if not transcribe_data:
            raise ValueError(f"Missing '{transcribe_key}' stage output")
        transcript_v1 = _parse_transcript(transcribe_data)
        align_output = _try_parse_align(align_data) if align_data else None

        segments_source, ch_word_ts, ch_warnings = _select_segments(
            align_output=align_output,
            transcript=transcript_v1,
        )
        if ch_word_ts:
            word_timestamps_available = True
        pipeline_warnings.extend(ch_warnings)

        # Build annotated segment dicts with channel/speaker info
        for seg in segments_source:
            seg_dict = _extract_segment_fields(seg)
            seg_dict["speaker"] = speaker_id
            seg_dict["channel"] = channel
            seg_dict["_word_ts"] = ch_word_ts
            all_channel_segments.append(seg_dict)

    # Sort all segments by start time to interleave channels
    all_channel_segments.sort(key=lambda s: s["start"])

    # Build MergedSegment list
    segments: list[MergedSegment] = []
    for idx, seg_dict in enumerate(all_channel_segments):
        words: list[Word] | None = None
        if seg_dict["_word_ts"] and seg_dict.get("words"):
            words = _normalize_words(seg_dict["words"])

        segment = MergedSegment(
            id=f"seg_{idx:03d}",
            start=seg_dict["start"],
            end=seg_dict["end"],
            text=seg_dict["text"],
            speaker=seg_dict["speaker"],
            language=seg_dict.get("language"),
            words=words,
            tokens=seg_dict.get("tokens")
            if isinstance(seg_dict.get("tokens"), list)
            else None,
            temperature=seg_dict.get("temperature"),
            avg_logprob=seg_dict.get("avg_logprob"),
            compression_ratio=seg_dict.get("compression_ratio"),
            no_speech_prob=seg_dict.get("no_speech_prob"),
            emotion=None,
            emotion_confidence=None,
            events=[],
        )
        segments.append(segment)

    # Build full text from interleaved segments
    text = " ".join(seg.text.strip() for seg in segments if seg.text.strip())

    # Build speakers with channel attribute
    speakers = [
        Speaker(id=f"SPEAKER_{ch:02d}", label=None, channel=ch)
        for ch in range(channel_count)
    ]

    # Apply known speaker names
    if isinstance(known_speaker_names, list) and known_speaker_names:
        _apply_known_speaker_names(segments, speakers, known_speaker_names)

    # Build merged languages list (sorted by confidence descending)
    languages: list[LanguageInfo] | None = None
    if all_languages:
        languages = sorted(
            all_languages.values(), key=lambda li: li.confidence, reverse=True
        )

    # Build metadata
    now = datetime.now(UTC).isoformat()
    metadata = TranscriptMetadata(
        audio_duration=audio_duration,
        audio_channels=audio_channels or channel_count,
        sample_rate=sample_rate,
        language=language,
        language_confidence=round(language_confidence, 3),
        languages=languages,
        word_timestamps=word_timestamps_available,
        word_timestamps_requested=word_timestamps_requested,
        speaker_detection=SpeakerDetectionMode.PER_CHANNEL,
        speaker_count=channel_count,
        created_at=now,
        completed_at=now,
        pipeline_stages=pipeline_stages or [],
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
        "per_channel_transcript_assembled",
        job_id=job_id,
        channel_count=channel_count,
        segment_count=len(segments),
        char_count=len(text),
        language=language,
        word_timestamps=word_timestamps_available,
    )

    return transcript


def _extract_segment_fields(seg: Segment | TranscriptSegment) -> dict[str, Any]:
    """Extract segment fields into a plain dict from a typed segment."""
    if isinstance(seg, TranscriptSegment):
        return {
            "start": seg.start,
            "end": seg.end,
            "text": seg.text,
            "words": seg.words,
            "language": seg.language,
            "tokens": seg.metadata.get("tokens"),
            "temperature": seg.metadata.get("temperature"),
            "avg_logprob": seg.metadata.get("avg_logprob"),
            "compression_ratio": seg.metadata.get("compression_ratio"),
            "no_speech_prob": seg.metadata.get("no_speech_prob"),
        }
    elif isinstance(seg, Segment):
        return {
            "start": seg.start,
            "end": seg.end,
            "text": seg.text,
            "words": seg.words,
            "language": seg.language,
            "tokens": seg.tokens,
            "temperature": seg.temperature,
            "avg_logprob": seg.avg_logprob,
            "compression_ratio": seg.compression_ratio,
            "no_speech_prob": seg.no_speech_prob,
        }
    else:
        raise TypeError(f"Unexpected segment type: {type(seg)}")


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
) -> tuple[str, str, float, list[LanguageInfo] | None]:
    """Extract text, language, confidence, and languages from transcribe output."""
    text = transcribe_data.get("text", "")
    language = transcribe_data.get("language", "en")
    language_confidence_raw = transcribe_data.get("language_confidence")
    language_confidence = (
        language_confidence_raw if language_confidence_raw is not None else 1.0
    )
    # Extract code-switching language list if present
    raw_languages = transcribe_data.get("languages")
    languages: list[LanguageInfo] | None = None
    if isinstance(raw_languages, list) and raw_languages:
        parsed: list[LanguageInfo] = []
        for entry in raw_languages:
            if isinstance(entry, dict):
                try:
                    parsed.append(LanguageInfo.model_validate(entry))
                except Exception:
                    pass
            elif isinstance(entry, LanguageInfo):
                parsed.append(entry)
        if parsed:
            languages = parsed
    return text, language, language_confidence, languages


def _parse_transcript(data: dict[str, Any]) -> Transcript:
    """Parse transcribe data into a Transcript. Raises on invalid data."""
    return Transcript.model_validate(data)


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
    align_output: AlignOutput | None,
    transcript: Transcript,
) -> tuple[list[Segment] | list[TranscriptSegment], bool, list]:
    """Select the best available segments and determine word timestamp availability.

    Returns:
        Tuple of (segments_source, word_timestamps_available, pipeline_warnings).
    """
    pipeline_warnings: list = []

    if align_output is not None:
        if align_output.skipped:
            logger.warning("alignment_skipped", reason=align_output.skip_reason)
            pipeline_warnings.extend(align_output.warnings)
            segments_source: list[Segment] | list[TranscriptSegment] = list(
                transcript.segments
            )
            word_timestamps_available = False
        else:
            segments_source = list(align_output.segments)
            word_timestamps_available = align_output.word_timestamps
    else:
        segments_source = list(transcript.segments)
        word_timestamps_available = any(s.words for s in transcript.segments)

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


def _smooth_diarization_turns(
    turns: list[SpeakerTurn],
    min_duration: float = MIN_TURN_DURATION_S,
) -> list[SpeakerTurn]:
    """Merge micro-turns into neighbours to eliminate overlap chatter.

    Pyannote's frame-level classifier can oscillate rapidly between speakers
    during crosstalk, producing turns of 20-70 ms that create single-word
    segments after word assignment.  This function:

    1. Absorbs any turn shorter than *min_duration* into the longer adjacent
       turn (preferring the neighbour with more temporal overlap, falling back
       to the longer neighbour).
    2. Merges consecutive same-speaker turns that are now adjacent or
       overlapping.

    The result is a cleaner turn list that still preserves genuine short
    utterances (back-channels, interjections) as long as they exceed the
    threshold.
    """
    if not turns or min_duration <= 0:
        return turns

    sorted_turns = sorted(turns, key=lambda t: (t.start, t.end))

    # --- Step 1: absorb micro-turns -------------------------------------------
    kept: list[SpeakerTurn] = []
    absorbed_indices: set[int] = set()

    for idx, turn in enumerate(sorted_turns):
        if (turn.end - turn.start) >= min_duration:
            kept.append(turn)
        else:
            absorbed_indices.add(idx)

    if not absorbed_indices:
        # Nothing to smooth — skip to merge step.
        return _merge_adjacent_turns(sorted_turns)

    # If every turn is micro, keep the longest one so we don't lose all
    # diarization information.
    if not kept:
        longest = max(sorted_turns, key=lambda t: t.end - t.start)
        kept.append(longest)
        absorbed_indices.discard(sorted_turns.index(longest))

    # Map each kept turn to its original index so we can look it up after
    # in-place rebuilds (Pydantic models are immutable, so we create new
    # objects when extending).
    kept_by_orig_idx: dict[int, int] = {}
    for orig_idx, _turn in enumerate(sorted_turns):
        if orig_idx not in absorbed_indices:
            kept_by_orig_idx[orig_idx] = len(kept_by_orig_idx)

    def _turn_overlap(a: SpeakerTurn, b: SpeakerTurn) -> float:
        return max(0.0, min(a.end, b.end) - max(a.start, b.start))

    # For each absorbed micro-turn, extend the best neighbour to cover it.
    for idx in sorted(absorbed_indices):
        micro = sorted_turns[idx]

        # Find immediate non-absorbed neighbours by original index.
        prev_orig: int | None = None
        nxt_orig: int | None = None
        for p in range(idx - 1, -1, -1):
            if p not in absorbed_indices:
                prev_orig = p
                break
        for n in range(idx + 1, len(sorted_turns)):
            if n not in absorbed_indices:
                nxt_orig = n
                break

        prev = kept[kept_by_orig_idx[prev_orig]] if prev_orig is not None else None
        nxt = kept[kept_by_orig_idx[nxt_orig]] if nxt_orig is not None else None

        # Pick the neighbour with more temporal overlap with the micro-turn;
        # tie-break by longer duration; last resort: whichever exists.
        target: SpeakerTurn | None = None
        target_k_idx: int | None = None
        if prev and nxt:
            ov_prev = _turn_overlap(prev, micro)
            ov_nxt = _turn_overlap(nxt, micro)
            if ov_prev > ov_nxt:
                target, target_k_idx = prev, kept_by_orig_idx[prev_orig]  # type: ignore[arg-type]
            elif ov_nxt > ov_prev:
                target, target_k_idx = nxt, kept_by_orig_idx[nxt_orig]  # type: ignore[arg-type]
            else:
                if (prev.end - prev.start) >= (nxt.end - nxt.start):
                    target, target_k_idx = prev, kept_by_orig_idx[prev_orig]  # type: ignore[arg-type]
                else:
                    target, target_k_idx = nxt, kept_by_orig_idx[nxt_orig]  # type: ignore[arg-type]
        elif prev:
            target, target_k_idx = prev, kept_by_orig_idx[prev_orig]  # type: ignore[arg-type]
        elif nxt:
            target, target_k_idx = nxt, kept_by_orig_idx[nxt_orig]  # type: ignore[arg-type]

        if target is not None and target_k_idx is not None:
            kept[target_k_idx] = SpeakerTurn(
                start=min(target.start, micro.start),
                end=max(target.end, micro.end),
                speaker=target.speaker,
                confidence=target.confidence,
                overlapping_speakers=target.overlapping_speakers,
            )

    # --- Step 2: merge consecutive same-speaker turns -------------------------
    return _merge_adjacent_turns(sorted(kept, key=lambda t: (t.start, t.end)))


def _merge_adjacent_turns(turns: list[SpeakerTurn]) -> list[SpeakerTurn]:
    """Merge consecutive turns with the same speaker that overlap or are adjacent."""
    if not turns:
        return []

    merged: list[SpeakerTurn] = [turns[0]]
    for turn in turns[1:]:
        prev = merged[-1]
        if turn.speaker == prev.speaker and turn.start <= prev.end:
            merged[-1] = SpeakerTurn(
                start=prev.start,
                end=max(prev.end, turn.end),
                speaker=prev.speaker,
                confidence=prev.confidence,
                overlapping_speakers=prev.overlapping_speakers,
            )
        else:
            merged.append(turn)

    return merged


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


def _normalize_transcript_words(words: list) -> list[Word]:
    """Normalize TranscriptWord or Word objects into pipeline Word objects."""
    from dalston.common.pipeline_types import TranscriptWord as TW

    result = []
    for w in words:
        if isinstance(w, Word):
            result.append(w)
        elif isinstance(w, TW):
            result.append(
                Word(
                    text=w.text,
                    start=w.start,
                    end=w.end,
                    confidence=w.confidence,
                    alignment_method=w.alignment_method,
                    language=w.language,
                )
            )
        elif isinstance(w, dict):
            try:
                result.append(Word.model_validate(w))
            except Exception:
                pass
    return result or []


def _build_speaker_windows(
    seg_start: float,
    seg_end: float,
    turns: list[SpeakerTurn],
) -> list[tuple[float, float, str | None]]:
    """Build non-overlapping windows across a segment using diarization boundaries."""
    if seg_end <= seg_start:
        return []

    boundaries = {seg_start, seg_end}
    for turn in turns:
        if turn.end <= seg_start or turn.start >= seg_end:
            continue
        boundaries.add(max(seg_start, turn.start))
        boundaries.add(min(seg_end, turn.end))

    ordered = sorted(boundaries)
    windows: list[tuple[float, float, str | None]] = []
    for idx in range(len(ordered) - 1):
        win_start = ordered[idx]
        win_end = ordered[idx + 1]
        if win_end <= win_start:
            continue
        speaker = _find_speaker_by_overlap(win_start, win_end, turns)
        if windows and windows[-1][2] == speaker:
            prev_start, _, prev_speaker = windows[-1]
            windows[-1] = (prev_start, win_end, prev_speaker)
        else:
            windows.append((win_start, win_end, speaker))

    return windows


def _find_nearest_turn_speaker(at_time: float, turns: list[SpeakerTurn]) -> str | None:
    """Find nearest speaker turn to a timestamp."""
    nearest_speaker: str | None = None
    nearest_distance: float | None = None

    for turn in turns:
        if turn.start <= at_time <= turn.end:
            return turn.speaker
        if at_time < turn.start:
            distance = turn.start - at_time
        else:
            distance = at_time - turn.end
        if nearest_distance is None or distance < nearest_distance:
            nearest_distance = distance
            nearest_speaker = turn.speaker

    return nearest_speaker


def _assign_speaker_to_word(word: Word, turns: list[SpeakerTurn]) -> str | None:
    """Assign speaker to a word using overlap, then nearest-turn fallback."""
    if not turns:
        return None

    if word.end < word.start:
        return None

    # Zero-duration words are common at segment tails; treat as point assignments.
    if word.end == word.start:
        return _find_nearest_turn_speaker(word.start, turns)

    speaker = _find_speaker_by_overlap(word.start, word.end, turns)
    if speaker is not None:
        return speaker

    midpoint = (word.start + word.end) / 2.0
    return _find_nearest_turn_speaker(midpoint, turns)


def _split_words_by_speaker(
    words: list[Word],
    turns: list[SpeakerTurn],
) -> list[dict[str, Any]]:
    """Split words into contiguous runs by assigned speaker."""
    if not words:
        return []

    sorted_words = sorted(words, key=lambda w: (w.start, w.end))
    split_parts: list[dict[str, Any]] = []

    current_speaker: str | None = None
    current_words: list[Word] = []

    for word in sorted_words:
        if word.end < word.start:
            continue
        speaker = _assign_speaker_to_word(word, turns)
        if current_words and speaker != current_speaker:
            text = " ".join(w.text.strip() for w in current_words if w.text.strip())
            if text:
                split_parts.append(
                    {
                        "start": current_words[0].start,
                        "end": max(current_words[-1].end, current_words[-1].start),
                        "text": text,
                        "speaker": current_speaker,
                        "words": list(current_words),
                    }
                )
            current_words = []
        current_words.append(word)
        current_speaker = speaker

    if current_words:
        text = " ".join(w.text.strip() for w in current_words if w.text.strip())
        if text:
            split_parts.append(
                {
                    "start": current_words[0].start,
                    "end": max(current_words[-1].end, current_words[-1].start),
                    "text": text,
                    "speaker": current_speaker,
                    "words": list(current_words),
                }
            )

    return split_parts


def _merge_short_splits(
    parts: list[dict[str, Any]],
    min_words: int = MIN_SEGMENT_WORDS,
) -> list[dict[str, Any]]:
    """Merge splits with fewer than *min_words* into the closest neighbour.

    After word-level speaker splitting, genuine but very brief overlapping
    speech can produce orphan 1-2 word segments (e.g. a single "i" from a
    cross-talk moment).  This pass absorbs them into the temporally closest
    adjacent part, preferring the neighbour that already has more words.
    """
    if len(parts) <= 1 or min_words <= 1:
        return parts

    merged: list[dict[str, Any]] = list(parts)

    changed = True
    while changed:
        changed = False
        for i in range(len(merged)):
            word_count = len(merged[i].get("words") or merged[i]["text"].split())
            if word_count >= min_words:
                continue

            # Find best neighbour to absorb into.
            prev = merged[i - 1] if i > 0 else None
            nxt = merged[i + 1] if i < len(merged) - 1 else None
            target_idx: int | None = None

            if prev and nxt:
                prev_words = len(prev.get("words") or prev["text"].split())
                nxt_words = len(nxt.get("words") or nxt["text"].split())
                target_idx = (i - 1) if prev_words >= nxt_words else (i + 1)
            elif prev:
                target_idx = i - 1
            elif nxt:
                target_idx = i + 1

            if target_idx is None:
                continue

            t = merged[target_idx]
            s = merged[i]

            # Combine words and text.
            if target_idx < i:
                combined_words = (t.get("words") or []) + (s.get("words") or [])
                combined_text = (t["text"] + " " + s["text"]).strip()
                new_start = t["start"]
                new_end = max(s["end"], t["end"])
            else:
                combined_words = (s.get("words") or []) + (t.get("words") or [])
                combined_text = (s["text"] + " " + t["text"]).strip()
                new_start = min(s["start"], t["start"])
                new_end = t["end"]

            merged[target_idx] = {
                "start": new_start,
                "end": new_end,
                "text": combined_text,
                "speaker": t["speaker"],
                "words": combined_words if combined_words else None,
            }
            merged.pop(i)
            changed = True
            break

    return merged


def _split_text_across_windows(
    seg_text: str,
    seg_start: float,
    seg_end: float,
    windows: list[tuple[float, float, str | None]],
) -> list[str]:
    """Split segment text across windows proportionally by token index."""
    if not windows:
        return []

    tokens = [token for token in seg_text.split() if token]
    if not tokens:
        return ["" for _ in windows]

    if len(windows) == 1:
        return [" ".join(tokens)]

    duration = seg_end - seg_start
    if duration <= 0:
        return [" ".join(tokens)] + ["" for _ in range(len(windows) - 1)]

    buckets: list[list[str]] = [[] for _ in windows]
    window_idx = 0
    token_count = len(tokens)
    for idx, token in enumerate(tokens):
        midpoint = seg_start + duration * ((idx + 0.5) / token_count)
        while window_idx < len(windows) - 1 and midpoint >= windows[window_idx][1]:
            window_idx += 1
        buckets[window_idx].append(token)

    return [" ".join(bucket) for bucket in buckets]


def _split_segment_by_diarization(
    *,
    seg_start: float,
    seg_end: float,
    seg_text: str,
    words: list[Word] | None,
    diarization_turns: list[SpeakerTurn],
) -> list[dict[str, Any]]:
    """Split one transcript segment by diarization speaker windows."""
    if not diarization_turns:
        return [
            {
                "start": seg_start,
                "end": seg_end,
                "text": seg_text,
                "speaker": None,
                "words": words,
            }
        ]

    windows = _build_speaker_windows(seg_start, seg_end, diarization_turns)
    if not windows:
        return [
            {
                "start": seg_start,
                "end": seg_end,
                "text": seg_text,
                "speaker": _find_speaker_by_overlap(
                    seg_start, seg_end, diarization_turns
                ),
                "words": words,
            }
        ]

    if len(windows) == 1:
        return [
            {
                "start": seg_start,
                "end": seg_end,
                "text": seg_text,
                "speaker": windows[0][2],
                "words": words,
            }
        ]

    # Best path: word-aware splitting aligned to diarization windows.
    if words:
        split_with_words = _split_words_by_speaker(words, diarization_turns)
        if split_with_words:
            return _merge_short_splits(split_with_words)

    # Fallback without words: split text across diarization windows.
    resolved_windows: list[tuple[float, float, str | None]] = []
    for win_start, win_end, speaker in windows:
        resolved_speaker = speaker
        if resolved_speaker is None:
            resolved_speaker = _find_nearest_turn_speaker(
                (win_start + win_end) / 2.0, diarization_turns
            )
        if resolved_windows and resolved_windows[-1][2] == resolved_speaker:
            prev_start, _, prev_speaker = resolved_windows[-1]
            resolved_windows[-1] = (prev_start, win_end, prev_speaker)
        else:
            resolved_windows.append((win_start, win_end, resolved_speaker))

    split_text = _split_text_across_windows(
        seg_text,
        seg_start,
        seg_end,
        resolved_windows,
    )
    split_without_words: list[dict[str, Any]] = []
    for (win_start, win_end, speaker), text in zip(
        resolved_windows, split_text, strict=True
    ):
        split_without_words.append(
            {
                "start": win_start,
                "end": win_end,
                "text": text,
                "speaker": speaker,
                "words": None,
            }
        )

    return split_without_words or [
        {
            "start": seg_start,
            "end": seg_end,
            "text": seg_text,
            "speaker": _find_speaker_by_overlap(seg_start, seg_end, diarization_turns),
            "words": words,
        }
    ]


def _build_merged_segments(
    *,
    segments_source: list[Segment] | list[TranscriptSegment],
    diarization_turns: list[SpeakerTurn],
    word_timestamps_available: bool,
) -> list[MergedSegment]:
    """Build MergedSegment list with IDs and speaker assignments."""
    segments: list[MergedSegment] = []

    for seg in segments_source:
        seg_words: Any = None
        if isinstance(seg, TranscriptSegment):
            seg_start = seg.start
            seg_end = seg.end
            seg_text = seg.text
            seg_words = seg.words
            seg_tokens = seg.metadata.get("tokens")
            seg_temperature = seg.metadata.get("temperature")
            seg_avg_logprob = seg.metadata.get("avg_logprob")
            seg_compression_ratio = seg.metadata.get("compression_ratio")
            seg_no_speech_prob = seg.metadata.get("no_speech_prob")
        elif isinstance(seg, Segment):
            seg_start = seg.start
            seg_end = seg.end
            seg_text = seg.text
            seg_words = seg.words
            seg_tokens = seg.tokens
            seg_temperature = seg.temperature
            seg_avg_logprob = seg.avg_logprob
            seg_compression_ratio = seg.compression_ratio
            seg_no_speech_prob = seg.no_speech_prob
        else:
            raise TypeError(f"Unexpected segment type: {type(seg)}")

        # Normalize words once. They are used for splitting when available.
        normalized_words: list[Word] | None = None
        if seg_words:
            if isinstance(seg, TranscriptSegment):
                normalized_words = _normalize_transcript_words(seg_words)
            else:
                normalized_words = _normalize_words(seg_words)

        # Extract per-segment language (code-switching)
        seg_language: str | None = None
        if isinstance(seg, TranscriptSegment):
            seg_language = seg.language
        elif isinstance(seg, Segment):
            seg_language = seg.language

        split_parts = _split_segment_by_diarization(
            seg_start=seg_start,
            seg_end=seg_end,
            seg_text=seg_text,
            words=normalized_words,
            diarization_turns=diarization_turns,
        )

        for part in split_parts:
            output_words: list[Word] | None = None
            part_words = part.get("words")
            if (
                word_timestamps_available
                and isinstance(part_words, list)
                and part_words
            ):
                output_words = part_words

            segment = MergedSegment(
                id=f"seg_{len(segments):03d}",
                start=float(part["start"]),
                end=float(part["end"]),
                text=str(part["text"]),
                speaker=part["speaker"],
                language=seg_language,
                words=output_words,
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
