"""Phoneme-level forced alignment pipeline.

Aligns transcription segments to audio using wav2vec2-based CTC forced
alignment. Produces word-level (and optionally character-level) timestamps.

This is a standalone reimplementation of the alignment algorithm described
in the WhisperX paper (Bain et al., INTERSPEECH 2023), without depending
on the whisperx package.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, TypedDict

import numpy as np
import torch
from ctc_forced_align import CharSegment, backtrack, build_trellis, merge_repeats
from model_loader import AlignModelMetadata

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000

LANGUAGES_WITHOUT_SPACES = {"ja", "zh"}


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class InputSegment(TypedDict):
    """A transcription segment to be aligned."""

    start: float
    end: float
    text: str


@dataclass
class AlignedWord:
    """A word with aligned timestamps."""

    word: str
    start: float | None = None
    end: float | None = None
    score: float | None = None


@dataclass
class AlignedSegment:
    """A segment with aligned word-level timestamps."""

    start: float
    end: float
    text: str
    words: list[AlignedWord] = field(default_factory=list)


@dataclass
class AlignResult:
    """Result from the alignment pipeline."""

    segments: list[AlignedSegment]
    word_segments: list[AlignedWord]


# ---------------------------------------------------------------------------
# Main alignment function
# ---------------------------------------------------------------------------


def align(
    transcript: list[InputSegment],
    model: Any,
    metadata: AlignModelMetadata,
    audio: np.ndarray | torch.Tensor,
    device: str,
    interpolate_method: str = "nearest",
    return_char_alignments: bool = False,
) -> AlignResult:
    """Align transcription segments to audio using CTC forced alignment.

    For each segment in the transcript, the algorithm:
        1. Preprocesses the text to map characters to model vocabulary tokens.
        2. Extracts the relevant audio slice and runs the wav2vec2 model.
        3. Builds a CTC trellis and backtracks to find the optimal alignment.
        4. Converts frame indices to timestamps and aggregates into words.

    Args:
        transcript: List of segments with ``start``, ``end``, ``text`` keys.
        model: A loaded wav2vec2 alignment model.
        metadata: Model metadata including character dictionary.
        audio: Audio waveform, 16 kHz mono. Shape ``(samples,)`` or ``(1, samples)``.
        device: Torch device string.
        interpolate_method: Method for filling NaN timestamps (``"nearest"``).
        return_char_alignments: If True, include character-level detail
            (not used externally but kept for parity).

    Returns:
        AlignResult with aligned segments and a flat word list.
    """
    if not isinstance(audio, torch.Tensor):
        audio = torch.from_numpy(audio)
    if len(audio.shape) == 1:
        audio = audio.unsqueeze(0)

    max_duration = audio.shape[1] / SAMPLE_RATE
    dictionary = metadata.dictionary
    pipeline_type = metadata.pipeline_type
    lang = metadata.language

    # ------------------------------------------------------------------
    # Phase 1: Preprocess all segments
    # ------------------------------------------------------------------
    segment_data: dict[int, _SegmentData] = {}
    for idx, seg in enumerate(transcript):
        segment_data[idx] = _preprocess_segment(seg["text"], dictionary, lang)

    # ------------------------------------------------------------------
    # Phase 2: Align each segment
    # ------------------------------------------------------------------
    aligned_segments: list[AlignedSegment] = []

    for idx, seg in enumerate(transcript):
        t1 = seg["start"]
        t2 = seg["end"]
        text = seg["text"]
        sd = segment_data[idx]

        # Can't align if no valid characters or segment is beyond audio
        if not sd.clean_chars:
            logger.warning(
                "No alignable characters in segment '%s', using original timestamps",
                text[:60],
            )
            aligned_segments.append(AlignedSegment(start=t1, end=t2, text=text))
            continue

        if t1 >= max_duration:
            logger.warning(
                "Segment start (%.2fs) exceeds audio duration (%.2fs), skipping",
                t1,
                max_duration,
            )
            aligned_segments.append(AlignedSegment(start=t1, end=t2, text=text))
            continue

        # Build token sequence for the cleaned characters
        text_clean = "".join(sd.clean_chars)
        tokens = [dictionary.get(c, -1) for c in text_clean]

        # Extract audio slice
        f1 = int(t1 * SAMPLE_RATE)
        f2 = int(t2 * SAMPLE_RATE)
        waveform = audio[:, f1:f2]

        # wav2vec2 needs at least 400 samples (~25ms)
        lengths = None
        if waveform.shape[-1] < 400:
            lengths = torch.as_tensor([waveform.shape[-1]]).to(device)
            waveform = torch.nn.functional.pad(waveform, (0, 400 - waveform.shape[-1]))

        # Run model forward pass
        with torch.inference_mode():
            if pipeline_type == "torchaudio":
                emissions, _ = model(waveform.to(device), lengths=lengths)
            else:
                emissions = model(waveform.to(device)).logits
            emissions = torch.log_softmax(emissions, dim=-1)

        emission = emissions[0].cpu().detach()

        # Determine blank token id
        blank_id = _find_blank_id(dictionary)

        # CTC forced alignment
        trellis = build_trellis(emission, tokens, blank_id)
        path = backtrack(trellis, emission, tokens, blank_id, beam_width=2)

        if path is None:
            logger.warning(
                "Backtrack failed for segment '%s', using original timestamps",
                text[:60],
            )
            aligned_segments.append(AlignedSegment(start=t1, end=t2, text=text))
            continue

        char_segments = merge_repeats(path, text_clean)

        # Convert frame indices to absolute timestamps
        duration = t2 - t1
        ratio = duration * waveform.size(1) / (trellis.size(0) - 1)

        char_timings = _assign_char_timestamps(text, sd, char_segments, ratio, t1, lang)

        # Aggregate characters into words
        words = _aggregate_words(char_timings, lang)

        # Interpolate NaN timestamps
        _interpolate_word_timestamps(words, method=interpolate_method)

        # Build the aligned segment
        seg_start = t1
        seg_end = t2
        if words:
            starts = [w.start for w in words if w.start is not None]
            ends = [w.end for w in words if w.end is not None]
            if starts:
                seg_start = min(starts)
            if ends:
                seg_end = max(ends)

        aligned_segments.append(
            AlignedSegment(
                start=round(seg_start, 3),
                end=round(seg_end, 3),
                text=text,
                words=words,
            )
        )

    # Flat word list
    all_words: list[AlignedWord] = []
    for seg in aligned_segments:
        all_words.extend(seg.words)

    return AlignResult(segments=aligned_segments, word_segments=all_words)


# ---------------------------------------------------------------------------
# Internal types and helpers
# ---------------------------------------------------------------------------


class _SegmentData(TypedDict):
    clean_chars: list[str]
    clean_char_indices: list[int]


@dataclass
class _CharTiming:
    """Timing for a single character in the original text."""

    char: str
    start: float | None
    end: float | None
    score: float | None
    word_idx: int


def _preprocess_segment(
    text: str,
    dictionary: dict[str, int],
    language: str,
) -> _SegmentData:
    """Map segment text to model vocabulary, tracking original indices.

    Characters present in the dictionary are kept; others become ``"*"``
    (wildcard). Leading/trailing whitespace is excluded.
    """
    num_leading = len(text) - len(text.lstrip())
    num_trailing = len(text) - len(text.rstrip())

    clean_chars: list[str] = []
    clean_char_indices: list[int] = []

    for idx, char in enumerate(text):
        if idx < num_leading or idx > len(text) - num_trailing - 1:
            continue

        lower = char.lower()
        if language not in LANGUAGES_WITHOUT_SPACES:
            lower = lower.replace(" ", "|")

        if lower in dictionary:
            clean_chars.append(lower)
        else:
            clean_chars.append("*")
        clean_char_indices.append(idx)

    return _SegmentData(
        clean_chars=clean_chars,
        clean_char_indices=clean_char_indices,
    )


def _find_blank_id(dictionary: dict[str, int]) -> int:
    """Determine the CTC blank token index from the model dictionary."""
    for token_name in ("[pad]", "<pad>"):
        if token_name in dictionary:
            return dictionary[token_name]
    return 0


def _assign_char_timestamps(
    text: str,
    sd: _SegmentData,
    char_segments: list[CharSegment],
    ratio: float,
    t1: float,
    language: str,
) -> list[_CharTiming]:
    """Assign absolute timestamps to each character in the original text."""
    timings: list[_CharTiming] = []
    word_idx = 0

    for idx, char in enumerate(text):
        start: float | None = None
        end: float | None = None
        score: float | None = None

        if idx in sd["clean_char_indices"]:
            pos = sd["clean_char_indices"].index(idx)
            cs = char_segments[pos]
            start = round(cs.start * ratio + t1, 3)
            end = round(cs.end * ratio + t1, 3)
            score = round(cs.score, 3)

        timings.append(
            _CharTiming(char=char, start=start, end=end, score=score, word_idx=word_idx)
        )

        # Advance word index
        if language in LANGUAGES_WITHOUT_SPACES:
            word_idx += 1
        elif idx == len(text) - 1 or text[idx + 1] == " ":
            word_idx += 1

    return timings


def _aggregate_words(
    char_timings: list[_CharTiming],
    language: str,
) -> list[AlignedWord]:
    """Group character timings into word-level timestamps."""
    words_by_idx: dict[int, list[_CharTiming]] = {}
    for ct in char_timings:
        words_by_idx.setdefault(ct.word_idx, []).append(ct)

    words: list[AlignedWord] = []
    for word_idx in sorted(words_by_idx):
        chars = words_by_idx[word_idx]
        word_text = "".join(c.char for c in chars).strip()
        if not word_text:
            continue

        # Exclude spaces from timing computation
        timed = [c for c in chars if c.char != " " and c.start is not None]
        if timed:
            word_start = min(c.start for c in timed)  # type: ignore[arg-type]
            word_end = max(c.end for c in timed)  # type: ignore[arg-type]
            scores = [c.score for c in timed if c.score is not None]
            word_score = round(sum(scores) / len(scores), 3) if scores else None
        else:
            word_start = None
            word_end = None
            word_score = None

        words.append(
            AlignedWord(
                word=word_text, start=word_start, end=word_end, score=word_score
            )
        )

    return words


def _interpolate_word_timestamps(
    words: list[AlignedWord],
    method: str = "nearest",
) -> None:
    """Fill NaN/None timestamps using interpolation.

    Modifies words in place. Uses linear interpolation between known
    timestamps, with forward-fill and backward-fill for edges.
    """
    if not words:
        return

    starts = [w.start for w in words]
    ends = [w.end for w in words]

    starts = _interpolate_nans(starts, method)
    ends = _interpolate_nans(ends, method)

    for i, word in enumerate(words):
        if word.start is None and starts[i] is not None:
            word.start = round(starts[i], 3)
        if word.end is None and ends[i] is not None:
            word.end = round(ends[i], 3)


def _interpolate_nans(
    values: list[float | None],
    method: str = "nearest",
) -> list[float | None]:
    """Interpolate None values in a list of floats.

    Uses numpy for interpolation with forward-fill and backward-fill
    for edge values.
    """
    if not values or all(v is None for v in values):
        return values

    arr = np.array([float("nan") if v is None else v for v in values], dtype=np.float64)
    valid_mask = ~np.isnan(arr)

    if valid_mask.sum() == 0:
        return values

    if valid_mask.sum() == len(arr):
        return [float(x) for x in arr]

    # Interpolate between valid points
    valid_indices = np.where(valid_mask)[0]
    if len(valid_indices) > 1:
        all_indices = np.arange(len(arr))
        arr = np.interp(all_indices, valid_indices, arr[valid_indices])
    else:
        # Only one valid point: fill everything with it
        arr[:] = arr[valid_indices[0]]

    return [float(x) for x in arr]
