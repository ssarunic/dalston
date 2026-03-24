"""Chunked diarization for long audio files.

Splits audio that exceeds a VRAM-safe duration into overlapping chunks,
diarizes each chunk independently, then links speakers across chunks
using embedding similarity.

The public entry point is :func:`run_chunked_diarization`.  It returns
the same ``(speakers, turns)`` tuple that the single-pass path produces,
so the caller's ``DiarizationResponse`` construction is unchanged.

Environment variables
---------------------
DALSTON_MAX_DIARIZE_CHUNK_S : float
    Maximum chunk duration in seconds (default 900 = 15 min).

Design notes
------------
* ``torch.cuda.empty_cache()`` is called between chunks on CUDA to free
  the pyannote reconstruction spike.  This does NOT reduce peak VRAM
  within a single chunk — if a chunk still OOMs, reduce the chunk size.
* Audio splitting uses ``ffmpeg -c copy`` which is valid because the
  input has already been through the prepare stage (16 kHz mono PCM WAV).
* Speaker linking uses ``pyannote/wespeaker-voxceleb-resnet34-LM``
  embeddings (already cached from the diarization pipeline).
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import structlog

from dalston.common.pipeline_types import SpeakerTurn

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_MAX_CHUNK_S: float = 900.0  # 15 minutes
DEFAULT_OVERLAP_S: float = 30.0
DEFAULT_MIN_CHUNK_S: float = 60.0
_SPEAKER_LINK_THRESHOLD: float = 0.7  # cosine distance for agglomerative clustering


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChunkSpec:
    """Describes one chunk extracted from the original audio."""

    index: int
    start: float  # seconds into original audio
    end: float  # seconds into original audio
    path: Path


@dataclass
class ChunkResult:
    """Diarization result for one chunk, before global speaker linking."""

    spec: ChunkSpec
    speakers: list[str]
    turns: list[SpeakerTurn]
    annotation: Any  # pyannote Annotation (for embedding extraction)
    embeddings: dict[str, np.ndarray] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Audio splitting
# ---------------------------------------------------------------------------


def compute_chunk_boundaries(
    duration: float,
    max_chunk_s: float = DEFAULT_MAX_CHUNK_S,
    overlap_s: float = DEFAULT_OVERLAP_S,
    min_chunk_s: float = DEFAULT_MIN_CHUNK_S,
) -> list[tuple[float, float]]:
    """Compute (start, end) boundaries for overlapping chunks.

    If the remaining tail would be shorter than *min_chunk_s*, it is
    absorbed into the previous chunk.
    """
    if duration <= max_chunk_s:
        return [(0.0, duration)]

    step = max_chunk_s - overlap_s
    boundaries: list[tuple[float, float]] = []
    start = 0.0

    while start < duration:
        end = min(start + max_chunk_s, duration)

        # Check if the NEXT chunk would be too short to be useful.
        # If so, extend the current chunk to cover the rest — but only
        # if the extension is small (within min_chunk_s of max_chunk_s).
        # Otherwise, just let the small trailing chunk exist.
        next_start = start + step
        next_chunk_len = duration - next_start
        extended_len = duration - start
        if (
            end < duration
            and 0 < next_chunk_len < min_chunk_s
            and extended_len <= max_chunk_s + min_chunk_s
        ):
            end = duration

        boundaries.append((start, end))
        if end >= duration:
            break
        start += step

    return boundaries


def get_audio_duration(audio_path: Path) -> float:
    """Probe audio duration.

    Uses the ``wave`` stdlib module for WAV files (the common case after
    the prepare stage).  Falls back to ``soundfile`` for other formats.
    """
    import wave

    try:
        with wave.open(str(audio_path), "rb") as wf:
            return wf.getnframes() / wf.getframerate()
    except wave.Error:
        pass

    # Fallback for non-WAV formats
    try:
        import soundfile as sf

        return sf.info(str(audio_path)).duration
    except ImportError:
        raise RuntimeError(
            f"Cannot probe duration of {audio_path}: "
            "not a WAV file and soundfile is not installed"
        ) from None


def extract_chunk(
    audio_path: Path,
    start_s: float,
    end_s: float,
    work_dir: Path,
    index: int,
) -> Path:
    """Extract an audio chunk using ffmpeg.

    Uses ``-c copy`` for zero-overhead slicing.  This is valid because
    the input is always PCM WAV from the prepare stage.
    """
    out = work_dir / f"chunk_{index:04d}.wav"
    duration = end_s - start_s
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-ss",
        f"{start_s:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(audio_path),
        "-c",
        "copy",
        str(out),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"ffmpeg chunk extraction timed out (300s) for chunk {index}"
        ) from None
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg chunk extraction failed for chunk {index}: {result.stderr}"
        )
    return out


# ---------------------------------------------------------------------------
# Speaker embedding extraction
# ---------------------------------------------------------------------------


def load_embedding_model(hf_token: str, device: str) -> Any:
    """Load the wespeaker embedding model via pyannote Inference.

    Returns a ``pyannote.audio.Inference`` instance, or ``None`` if
    the model cannot be loaded (embedding-based linking is then skipped).
    """
    try:
        from pyannote.audio import Inference

        # pyannote 4.0 Inference uses 'use_auth_token', not 'token'
        # (Pipeline.from_pretrained uses 'token', but Inference does not)
        model = Inference(
            "pyannote/wespeaker-voxceleb-resnet34-LM",
            window="whole",
            use_auth_token=hf_token,
        )
        if device in ("cuda", "mps"):
            import torch

            model.to(torch.device(device))
        logger.info("embedding_model_loaded", model="wespeaker-voxceleb-resnet34-LM")
        return model
    except Exception:
        logger.warning("embedding_model_load_failed", exc_info=True)
        return None


def extract_speaker_embeddings(
    audio_path: Path,
    annotation: Any,
    embedding_model: Any,
    chunk_offset: float = 0.0,
) -> dict[str, np.ndarray]:
    """Extract per-speaker centroid embeddings from a diarized chunk.

    For each speaker, crops the original audio to that speaker's turns
    and averages the embedding vectors into a centroid.

    Args:
        audio_path: Path to the chunk audio file.
        annotation: pyannote Annotation with speaker labels (times
            relative to the chunk, not the original audio).
        embedding_model: ``pyannote.audio.Inference`` instance.
        chunk_offset: Not used for embedding extraction (chunks are
            self-contained files), reserved for future use.

    Returns:
        Mapping of ``{speaker_label: centroid_embedding}``.
    """
    from pyannote.core import Segment

    embeddings_per_speaker: dict[str, list[np.ndarray]] = {}

    for turn, _, speaker in annotation.itertracks(yield_label=True):
        # Skip very short segments — degenerate embeddings
        if turn.duration < 1.0:
            continue
        try:
            emb = embedding_model.crop(
                {"audio": str(audio_path)},
                Segment(turn.start, turn.end),
            )
            # Result may be SlidingWindowFeature (.data attr), tensor, or ndarray
            if hasattr(emb, "data") and isinstance(emb.data, np.ndarray):
                emb_np = np.squeeze(emb.data)
            elif hasattr(emb, "numpy"):
                emb_np = np.squeeze(emb.numpy())
            elif hasattr(emb, "squeeze"):
                emb_np = np.atleast_1d(emb.squeeze())
            else:
                emb_np = np.squeeze(np.array(emb))
            if emb_np.ndim == 1 and emb_np.shape[0] > 0:
                embeddings_per_speaker.setdefault(speaker, []).append(emb_np)
        except Exception:
            logger.debug(
                "embedding_crop_failed", speaker=speaker, start=turn.start, end=turn.end
            )
            continue

    # Compute centroids
    centroids: dict[str, np.ndarray] = {}
    for speaker, embs in embeddings_per_speaker.items():
        centroids[speaker] = np.mean(embs, axis=0)

    return centroids


# ---------------------------------------------------------------------------
# Cross-chunk speaker linking
# ---------------------------------------------------------------------------


def link_speakers(
    chunk_results: list[ChunkResult],
) -> dict[int, dict[str, str]]:
    """Link speaker labels across chunks using embedding similarity.

    Uses agglomerative clustering on all per-speaker centroid embeddings
    to discover global speaker identities, then returns a per-chunk
    mapping from local labels to global ``SPEAKER_XX`` labels.

    Returns a dict keyed by ``chunk.spec.index`` (not list position) so
    that partial failures (missing chunks) don't cause index errors.

    If no embeddings are available (model failed to load), falls back to
    identity mapping — each chunk keeps its own labels, which may cause
    duplicate speaker IDs across chunks.
    """
    # Collect all centroids with provenance
    all_centroids: list[np.ndarray] = []
    provenance: list[tuple[int, str]] = []  # (chunk_index, local_speaker)

    for chunk in chunk_results:
        for speaker, centroid in chunk.embeddings.items():
            all_centroids.append(centroid)
            provenance.append((chunk.spec.index, speaker))

    if len(all_centroids) < 2:
        # 0 or 1 speaker total — no linking needed
        return _identity_maps(chunk_results)

    # Stack into matrix and cluster
    X = np.vstack(all_centroids)

    try:
        from sklearn.cluster import AgglomerativeClustering

        clustering = AgglomerativeClustering(
            n_clusters=None,
            distance_threshold=_SPEAKER_LINK_THRESHOLD,
            metric="cosine",
            linkage="average",
        )
        labels = clustering.fit_predict(X)
    except Exception:
        logger.warning("speaker_clustering_failed", exc_info=True)
        return _identity_maps(chunk_results)

    # Build per-chunk label maps keyed by chunk.spec.index
    # Assign global names by order of first appearance
    global_name_for_cluster: dict[int, str] = {}
    next_global = 0
    label_maps: dict[int, dict[str, str]] = {c.spec.index: {} for c in chunk_results}

    for i, (chunk_idx, local_speaker) in enumerate(provenance):
        cluster_id = int(labels[i])
        if cluster_id not in global_name_for_cluster:
            global_name_for_cluster[cluster_id] = f"SPEAKER_{next_global:02d}"
            next_global += 1
        label_maps[chunk_idx][local_speaker] = global_name_for_cluster[cluster_id]

    return label_maps


def _identity_maps(chunk_results: list[ChunkResult]) -> dict[int, dict[str, str]]:
    """Fallback: each local speaker keeps its label unchanged."""
    return {chunk.spec.index: {s: s for s in chunk.speakers} for chunk in chunk_results}


# ---------------------------------------------------------------------------
# Chunk merging
# ---------------------------------------------------------------------------


def merge_chunks(
    chunk_results: list[ChunkResult],
    label_maps: dict[int, dict[str, str]],
    overlap_s: float = DEFAULT_OVERLAP_S,
) -> tuple[list[str], list[SpeakerTurn]]:
    """Merge per-chunk turns into a single global timeline.

    For each chunk boundary, the overlap region is resolved by keeping
    turns from the earlier chunk up to the seam midpoint, and turns from
    the later chunk after it.

    Returns ``(sorted_speakers, sorted_turns)`` ready for
    ``DiarizationResponse``.
    """
    all_turns: list[SpeakerTurn] = []

    for chunk in chunk_results:
        offset = chunk.spec.start
        label_map = label_maps.get(chunk.spec.index, {})

        for turn in chunk.turns:
            global_start = round(turn.start + offset, 3)
            global_end = round(turn.end + offset, 3)
            global_speaker = label_map.get(turn.speaker, turn.speaker)

            all_turns.append(
                SpeakerTurn(
                    speaker=global_speaker,
                    start=global_start,
                    end=global_end,
                    confidence=turn.confidence,
                )
            )

    # Remove duplicate coverage in overlap regions.
    # For each pair of adjacent chunks, the seam is at
    # chunk[i].end - overlap_s/2 (midpoint of the overlap).
    # Drop turns from the later chunk that end before the seam,
    # and turns from the earlier chunk that start after the seam.
    if len(chunk_results) > 1 and overlap_s > 0:
        all_turns = _resolve_overlaps(chunk_results, all_turns, overlap_s)

    # Sort and deduplicate speakers
    all_turns.sort(key=lambda t: (t.start, t.end))
    speakers = sorted({t.speaker for t in all_turns})

    return speakers, all_turns


def _resolve_overlaps(
    chunk_results: list[ChunkResult],
    turns: list[SpeakerTurn],
    overlap_s: float,
) -> list[SpeakerTurn]:
    """Remove duplicate turns in overlap regions between adjacent chunks.

    Strategy: for each overlap region, compute the midpoint (seam).
    Keep turns from the earlier chunk up to the seam, and from the later
    chunk after the seam.  Turns straddling the seam are trimmed.
    """
    # Build seam points: the midpoint of each overlap region
    seams: list[float] = []
    for i in range(len(chunk_results) - 1):
        overlap_start = chunk_results[i + 1].spec.start
        overlap_end = min(
            chunk_results[i].spec.end,
            chunk_results[i + 1].spec.start + overlap_s,
        )
        seam = round((overlap_start + overlap_end) / 2, 3)
        seams.append(seam)

    # For each chunk, determine the valid time range
    # Chunk 0: [start, seam_0]
    # Chunk i: [seam_{i-1}, seam_i]
    # Chunk N: [seam_{N-1}, end]
    chunk_ranges: list[tuple[float, float]] = []
    for i, chunk in enumerate(chunk_results):
        range_start = seams[i - 1] if i > 0 else chunk.spec.start
        range_end = seams[i] if i < len(seams) else chunk.spec.end
        chunk_ranges.append((range_start, range_end))

    # Filter turns: keep only those whose midpoint falls within
    # their chunk's valid range
    kept: list[SpeakerTurn] = []
    for turn in turns:
        turn_mid = (turn.start + turn.end) / 2
        # Find which chunk's range this turn belongs to
        for range_start, range_end in chunk_ranges:
            if range_start <= turn_mid <= range_end:
                # Trim turn to fit within the valid range
                trimmed_start = max(turn.start, range_start)
                trimmed_end = min(turn.end, range_end)
                if trimmed_end > trimmed_start:
                    kept.append(
                        SpeakerTurn(
                            speaker=turn.speaker,
                            start=round(trimmed_start, 3),
                            end=round(trimmed_end, 3),
                            confidence=turn.confidence,
                        )
                    )
                break

    return kept


# ---------------------------------------------------------------------------
# Overlap stats from merged turns (no pyannote Annotation available)
# ---------------------------------------------------------------------------


def overlap_stats_from_turns(
    turns: list[SpeakerTurn],
) -> tuple[float, float]:
    """Compute overlap duration and ratio from a flat list of turns.

    Uses a sweep-line algorithm: sort all start/end events, track active
    speaker count.  Regions with count >= 2 are overlap.
    """
    if not turns:
        return 0.0, 0.0

    events: list[tuple[float, int]] = []  # (time, +1 for start / -1 for end)
    for t in turns:
        events.append((t.start, 1))
        events.append((t.end, -1))

    events.sort(key=lambda e: (e[0], e[1]))

    active = 0
    overlap_duration = 0.0
    total_speech = 0.0
    prev_time = 0.0

    for time, delta in events:
        if time > prev_time:
            dt = time - prev_time
            if active >= 2:
                overlap_duration += dt
            if active >= 1:
                total_speech += dt
        active += delta
        prev_time = time

    overlap_ratio = overlap_duration / total_speech if total_speech > 0 else 0.0
    return overlap_duration, overlap_ratio


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_chunked_diarization(
    pipeline: Any,
    audio_path: Path,
    diarization_params: dict[str, Any],
    *,
    hf_token: str,
    device: str,
    convert_annotation: Any,  # callable: annotation -> (speakers, turns)
    exclusive: bool = False,
    max_chunk_s: float = DEFAULT_MAX_CHUNK_S,
    overlap_s: float = DEFAULT_OVERLAP_S,
    log: structlog.BoundLogger | None = None,
) -> tuple[list[str], list[SpeakerTurn]]:
    """Run diarization on long audio by chunking, diarizing, and merging.

    This is the single public entry point.  It returns the same
    ``(speakers, turns)`` that the single-pass path produces.

    Args:
        pipeline: Loaded pyannote Pipeline instance.
        audio_path: Path to the full audio file (PCM WAV).
        diarization_params: Kwargs for ``pipeline()`` (min/max speakers).
        hf_token: HuggingFace token for the embedding model.
        device: ``"cuda"``, ``"mps"``, or ``"cpu"``.
        convert_annotation: Callable that converts a raw pyannote
            diarization result to ``(speakers, turns)``; this is the
            engine's existing ``_convert_annotation`` method.
        max_chunk_s: Maximum chunk duration in seconds.
        overlap_s: Overlap between adjacent chunks in seconds.
        log: Optional structured logger.

    Returns:
        ``(speakers, turns)`` with timestamps in the original audio's
        coordinate system.
    """
    _log = log or logger

    duration = get_audio_duration(audio_path)
    boundaries = compute_chunk_boundaries(duration, max_chunk_s, overlap_s)
    _log.info(
        "chunked_diarization_start",
        duration=round(duration, 1),
        num_chunks=len(boundaries),
        max_chunk_s=max_chunk_s,
        overlap_s=overlap_s,
    )

    # Load embedding model (lazy, once)
    embedding_model = load_embedding_model(hf_token, device)

    chunk_results: list[ChunkResult] = []

    with tempfile.TemporaryDirectory(prefix="dalston_diarize_chunks_") as tmp_dir:
        work_dir = Path(tmp_dir)

        for i, (start, end) in enumerate(boundaries):
            _log.info(
                "diarize_chunk", chunk=i, start=round(start, 1), end=round(end, 1)
            )

            # Extract chunk audio
            chunk_path = extract_chunk(audio_path, start, end, work_dir, i)
            spec = ChunkSpec(index=i, start=start, end=end, path=chunk_path)

            try:
                # Run pyannote on chunk
                raw_result = pipeline(str(chunk_path), **diarization_params)

                # Apply exclusive mode if requested (single speaker per segment)
                if exclusive and hasattr(raw_result, "exclusive_speaker_diarization"):
                    raw_result = raw_result.exclusive_speaker_diarization

                # Get the Annotation object (handles 4.0 DiarizationResponse)
                if hasattr(raw_result, "speaker_diarization"):
                    annotation = raw_result.speaker_diarization
                else:
                    annotation = raw_result

                # Convert to our types
                speakers, turns = convert_annotation(raw_result)

                # Extract speaker embeddings for cross-chunk linking
                embeddings: dict[str, np.ndarray] = {}
                if embedding_model is not None:
                    embeddings = extract_speaker_embeddings(
                        chunk_path, annotation, embedding_model
                    )

                chunk_results.append(
                    ChunkResult(
                        spec=spec,
                        speakers=speakers,
                        turns=turns,
                        annotation=annotation,
                        embeddings=embeddings,
                    )
                )

                _log.info(
                    "chunk_diarized",
                    chunk=i,
                    speakers=len(speakers),
                    turns=len(turns),
                    embeddings=len(embeddings),
                )
            except Exception:
                _log.warning("chunk_diarization_failed", chunk=i, exc_info=True)
                continue

            # Free reconstruction spike memory on CUDA
            if device == "cuda":
                try:
                    import torch

                    torch.cuda.empty_cache()
                except Exception:
                    pass

    if not chunk_results:
        raise RuntimeError("All chunks failed during chunked diarization")

    # Link speakers across chunks
    label_maps = link_speakers(chunk_results)

    # Merge into single timeline
    speakers, turns = merge_chunks(chunk_results, label_maps, overlap_s)

    _log.info(
        "chunked_diarization_complete",
        total_speakers=len(speakers),
        total_turns=len(turns),
        chunks_succeeded=len(chunk_results),
        chunks_total=len(boundaries),
    )

    return speakers, turns
