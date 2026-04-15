"""VAD-based audio chunker for long-audio transcription.

Wraps Silero VAD and exposes a chunk-oriented API to
:class:`BaseBatchTranscribeEngine` so any engine can opt into
long-audio chunking by overriding ``get_max_audio_duration_s()``.

Key invariants:

- Every :class:`AudioChunk` returned by :meth:`VadChunker.split` has
  ``duration <= max_chunk_duration_s``. Chunks always end at a speech
  boundary when one exists; when a single speech span exceeds the limit
  (continuous speech, no silence), the chunker force-splits at the
  boundary and logs a warning.
- Chunks are written as 16 kHz mono WAV files under a caller-supplied
  temp directory and are tracked for cleanup by the caller.
- The Silero VAD model is loaded lazily on first use and cached on the
  chunker instance. Subsequent calls reuse the loaded model.

Model loader resolution order (first one that works wins):

1. ``silero_vad`` pip package (**preferred**). Bundles both JIT and
   ONNX weights internally — fully offline once installed. This is
   what shipped images should depend on; it's listed in the relevant
   engine requirements.txt files.
2. ``DALSTON_SILERO_VAD_ONNX``: path to a prebuilt silero-vad ONNX
   file. Baked into images via ``docker/Dockerfile.base-nemo`` (and
   the vLLM ASR image). Loaded with :mod:`onnxruntime` by wrapping
   the raw windowed inference behind a ``get_speech_timestamps``
   helper. No internet required, no pip package required.
3. ``DALSTON_SILERO_VAD_PATH``: local path to a torch JIT ``.pt`` file
   or a torch.hub-style directory. For workers that stash a custom
   snapshot.
4. ``torch.hub.load("snakers4/silero-vad")``: online fallback for dev
   environments without the package or the baked-in model.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import structlog

from dalston.common.audio_defaults import (
    DEFAULT_MIN_SPEECH_MS,
    DEFAULT_SAMPLE_RATE,
    DEFAULT_VAD_THRESHOLD,
)
from dalston.engine_sdk.audio import write_wav_file
from dalston.engine_sdk.silero_vad import (
    WINDOW_SAMPLES_16K,
    SileroOnnxModel,
    load_silero_session,
)

logger = structlog.get_logger()

_SAMPLE_RATE = DEFAULT_SAMPLE_RATE


@dataclass
class SpeechSegment:
    """A detected speech region in an audio file.

    Times are in seconds relative to the start of the source file.
    """

    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass
class AudioChunk:
    """A chunk of audio ready for transcription.

    ``audio_path`` points to a 16 kHz mono WAV under the chunker's temp
    directory. ``offset`` is the start time of this chunk in the original
    audio; callers add it to per-chunk transcript timestamps when
    merging. ``duration`` is the chunk's own length in seconds.
    """

    audio_path: Path
    offset: float
    duration: float


class VadChunker:
    """Split audio into speech-bounded chunks using Silero VAD.

    Instances are single-threaded and per-request — the base engine
    creates a fresh chunker for each chunked task and discards it after
    ``split()`` returns. The Silero VAD model is lazy-loaded on first
    use; callers that want to avoid the repeated load cost should cache
    the ``VadChunker`` instance themselves.

    Invariant: every returned chunk has ``duration <= max_chunk_duration_s``.
    When a single speech span exceeds the limit (no internal silence),
    the chunker force-splits at the boundary and logs
    ``vad_force_split`` with the span duration.
    """

    def __init__(
        self,
        max_chunk_duration_s: float = 1500.0,
        min_speech_duration_s: float = DEFAULT_MIN_SPEECH_MS / 1000,
        min_silence_duration_s: float = 0.3,
        vad_threshold: float = DEFAULT_VAD_THRESHOLD,
    ) -> None:
        if max_chunk_duration_s <= 0:
            raise ValueError(
                f"max_chunk_duration_s must be positive, got {max_chunk_duration_s}"
            )
        self.max_chunk_duration_s = max_chunk_duration_s
        self.min_speech_duration_s = min_speech_duration_s
        self.min_silence_duration_s = min_silence_duration_s
        self.vad_threshold = vad_threshold
        self._model: Any | None = None
        self._get_speech_timestamps: Any | None = None

    def _ensure_model(self) -> None:
        """Lazy-load the Silero VAD model on first use.

        Resolution order (first success wins):

        1. ``silero_vad`` pip package (offline, preferred)
        2. ``DALSTON_SILERO_VAD_ONNX`` env var (offline, pre-baked)
        3. ``DALSTON_SILERO_VAD_PATH`` env var (local torch JIT)
        4. ``torch.hub.load`` (online fallback)
        """
        if self._model is not None:
            return
        if self._try_load_silero_package():
            return
        if self._try_load_onnx_env():
            return
        if self._try_load_torch_path_env():
            return
        self._load_from_torch_hub()

    def _try_load_silero_package(self) -> bool:
        """Load via the bundled silero_vad pip package (offline)."""
        try:
            from silero_vad import get_speech_timestamps, load_silero_vad
        except ImportError:
            return False
        try:
            self._model = load_silero_vad()
        except Exception as exc:
            logger.warning("silero_vad_pkg_load_failed", error=str(exc)[:200])
            return False
        self._get_speech_timestamps = get_speech_timestamps
        logger.info("silero_vad_loaded", backend="silero_vad_pkg")
        return True

    def _try_load_onnx_env(self) -> bool:
        """Load the pre-baked ONNX model via env var + onnxruntime.

        Matches the image contract documented in
        ``docker/Dockerfile.base-nemo`` which bakes
        ``/models/silero-vad/silero_vad.onnx`` and sets
        ``DALSTON_SILERO_VAD_ONNX`` to that path. Reuses the shared
        :class:`SileroOnnxModel` from :mod:`dalston.engine_sdk.silero_vad`
        so batch and realtime paths run identical inference code.
        """
        onnx_path = os.environ.get("DALSTON_SILERO_VAD_ONNX")
        if not onnx_path:
            return False
        path = Path(onnx_path)
        if not path.is_file():
            logger.warning(
                "silero_vad_onnx_env_missing",
                path=onnx_path,
                message="DALSTON_SILERO_VAD_ONNX does not point to a file",
            )
            return False
        try:
            session = load_silero_session(path)
        except RuntimeError as exc:
            logger.warning("silero_vad_onnx_session_failed", error=str(exc)[:200])
            return False
        model = SileroOnnxModel(session)
        model.reset_states(batch_size=1)
        self._model = model
        self._get_speech_timestamps = _get_speech_timestamps_offline
        logger.info("silero_vad_loaded", backend="onnxruntime", path=str(path))
        return True

    def _try_load_torch_path_env(self) -> bool:
        """Load a torch JIT file via ``DALSTON_SILERO_VAD_PATH``."""
        raw = os.environ.get("DALSTON_SILERO_VAD_PATH")
        if not raw:
            return False
        try:
            import torch
        except ImportError:
            return False
        p = Path(raw)
        if not p.exists():
            logger.warning("silero_vad_torch_path_missing", path=raw)
            return False
        try:
            if p.is_file():
                model = torch.jit.load(str(p), map_location="cpu")
                utils: tuple[Any, ...] = ()
            else:
                model, utils = torch.hub.load(
                    repo_or_dir=str(p),
                    source="local",
                    model="silero_vad",
                    onnx=False,
                    trust_repo=True,
                )
        except Exception as exc:
            logger.warning("silero_vad_torch_path_failed", error=str(exc)[:200])
            return False
        self._model = model
        if utils:
            self._get_speech_timestamps = utils[0]
        else:
            try:
                from silero_vad import get_speech_timestamps
            except ImportError:
                logger.warning(
                    "silero_vad_torch_path_missing_helper",
                    message="Loaded torch JIT model but silero_vad package is "
                    "missing for get_speech_timestamps",
                )
                return False
            self._get_speech_timestamps = get_speech_timestamps
        logger.info("silero_vad_loaded", backend="torch_path")
        return True

    def _load_from_torch_hub(self) -> None:
        """Final fallback: load via torch.hub (requires internet)."""
        try:
            import torch
        except ImportError as exc:
            raise RuntimeError(
                "VadChunker requires torch. Install torch in the engine image."
            ) from exc
        try:
            model, utils = torch.hub.load(
                repo_or_dir="snakers4/silero-vad",
                model="silero_vad",
                force_reload=False,
                onnx=False,
                trust_repo=True,
            )
        except Exception as exc:
            raise RuntimeError(
                "VadChunker could not load Silero VAD. Install the 'silero-vad' "
                "pip package, or set DALSTON_SILERO_VAD_ONNX to a baked model "
                "file, or ensure network access to torch.hub. Last error: "
                f"{exc}"
            ) from exc
        self._model = model
        self._get_speech_timestamps = utils[0]
        logger.info("silero_vad_loaded", backend="torch_hub")

    # ------------------------------------------------------------------
    # Speech detection
    # ------------------------------------------------------------------

    def detect_speech(self, audio_path: Path) -> list[SpeechSegment]:
        """Run Silero VAD on an audio file and return speech regions.

        Args:
            audio_path: Path to a 16 kHz mono WAV file (or any format
                that ``soundfile`` / ``librosa`` can read and resample).

        Returns:
            List of :class:`SpeechSegment` in ascending time order.
            Empty list if no speech is detected.
        """
        _, segments = self._load_and_detect(audio_path)
        return segments

    def _load_and_detect(
        self, audio_path: Path
    ) -> tuple[np.ndarray, list[SpeechSegment]]:
        """Decode the audio file once and run VAD on it.

        Returns both the decoded f32 mono 16 kHz array and the speech
        regions so that :meth:`split` can reuse the array for slicing
        without a second decode pass.
        """
        self._ensure_model()
        audio = self._load_audio_f32_mono_16k(audio_path)
        if audio.size == 0:
            return audio, []

        assert self._get_speech_timestamps is not None
        # The ``silero_vad`` pip package and ``torch.hub`` paths want a
        # torch tensor; the onnxruntime fallback and test fakes accept
        # numpy directly. Convert only when torch is importable — if it
        # isn't, by construction the only backends that could have
        # loaded are numpy-native ones, so passing the array through is
        # safe (and lets the chunker run in environments that don't
        # ship torch at all, including the CI unit tests).
        audio_input: Any = audio
        if self._get_speech_timestamps is not _get_speech_timestamps_offline:
            try:
                import torch

                audio_input = torch.from_numpy(audio)
            except ImportError:
                pass

        raw_segments = self._get_speech_timestamps(
            audio_input,
            self._model,
            threshold=self.vad_threshold,
            sampling_rate=_SAMPLE_RATE,
            min_speech_duration_ms=int(self.min_speech_duration_s * 1000),
            min_silence_duration_ms=int(self.min_silence_duration_s * 1000),
            return_seconds=False,
        )

        segments: list[SpeechSegment] = []
        for seg in raw_segments:
            start_s = float(seg["start"]) / _SAMPLE_RATE
            end_s = float(seg["end"]) / _SAMPLE_RATE
            if end_s > start_s:
                segments.append(SpeechSegment(start=start_s, end=end_s))
        return audio, segments

    # ------------------------------------------------------------------
    # Chunking
    # ------------------------------------------------------------------

    def split(
        self,
        audio_path: Path,
        temp_dir: Path,
        start_offset_s: float = 0.0,
    ) -> list[AudioChunk]:
        """Split audio into chunks at speech boundaries.

        Groups consecutive speech segments into chunks whose span from
        first VAD start to last VAD end does not exceed
        ``max_chunk_duration_s``. When a single speech segment exceeds
        the limit, it is force-split at the boundary and a warning is
        logged.

        Args:
            audio_path: Source audio file.
            temp_dir: Directory to write chunk WAV files into. Must
                exist and be writable. Caller is responsible for
                cleanup.
            start_offset_s: Resume boundary in seconds. Groups that end
                before it are skipped; groups that straddle it are
                **trimmed** at the boundary so the resulting chunk's
                ``offset`` equals ``start_offset_s`` and its audio slice
                starts there. Used by
                :meth:`BaseBatchTranscribeEngine._transcribe_chunks_with_backoff`
                on OOM retry to resume from the last failed chunk
                without reprocessing or dropping audio at the seam.

        Returns:
            List of :class:`AudioChunk` in temporal order. Empty list
            if the source has no speech past ``start_offset_s``.
        """
        temp_dir.mkdir(parents=True, exist_ok=True)
        audio_full, segments = self._load_and_detect(audio_path)
        if not segments:
            logger.info("vad_no_speech_detected", audio_path=str(audio_path))
            return []

        total_samples = audio_full.size

        capped: list[SpeechSegment] = []
        for seg in segments:
            if seg.duration <= self.max_chunk_duration_s:
                capped.append(seg)
                continue
            logger.warning(
                "vad_force_split",
                segment_duration_s=round(seg.duration, 3),
                max_chunk_duration_s=self.max_chunk_duration_s,
                message="Single speech span exceeds max_chunk_duration_s; "
                "force-splitting at the boundary (may cut mid-word)",
            )
            cursor = seg.start
            while cursor < seg.end:
                next_end = min(cursor + self.max_chunk_duration_s, seg.end)
                capped.append(SpeechSegment(start=cursor, end=next_end))
                cursor = next_end

        groups: list[list[SpeechSegment]] = []
        current: list[SpeechSegment] = []
        current_start: float | None = None
        for seg in capped:
            if current_start is None:
                current = [seg]
                current_start = seg.start
                continue
            span_end = seg.end
            if span_end - current_start <= self.max_chunk_duration_s:
                current.append(seg)
            else:
                groups.append(current)
                current = [seg]
                current_start = seg.start
        if current:
            groups.append(current)

        chunks: list[AudioChunk] = []
        for idx, group in enumerate(groups):
            start_s = group[0].start
            end_s = group[-1].end

            # Apply the resume boundary: drop groups fully before it,
            # trim groups that straddle it.
            if end_s <= start_offset_s + 1e-3:
                continue
            if start_s < start_offset_s:
                start_s = start_offset_s

            start_sample = max(0, int(round(start_s * _SAMPLE_RATE)))
            end_sample = min(total_samples, int(round(end_s * _SAMPLE_RATE)))
            if end_sample <= start_sample:
                continue
            slice_ = audio_full[start_sample:end_sample]
            out_path = temp_dir / f"chunk_{idx:04d}.wav"
            write_wav_file(out_path, slice_, sample_rate=_SAMPLE_RATE)
            chunks.append(
                AudioChunk(
                    audio_path=out_path,
                    offset=start_s,
                    duration=(end_sample - start_sample) / _SAMPLE_RATE,
                )
            )

        logger.info(
            "vad_chunks_prepared",
            audio_path=str(audio_path),
            chunk_count=len(chunks),
            total_audio_s=round(total_samples / _SAMPLE_RATE, 3),
            max_chunk_duration_s=self.max_chunk_duration_s,
            start_offset_s=round(start_offset_s, 3),
        )
        return chunks

    # ------------------------------------------------------------------
    # Audio I/O helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load_audio_f32_mono_16k(audio_path: Path) -> np.ndarray:
        """Load an audio file as float32 mono at 16 kHz.

        Uses ``soundfile`` for decode; resamples to 16 kHz via ``librosa``
        if needed. Downmixes multichannel to mono by averaging.
        """
        try:
            import soundfile as sf
        except ImportError as exc:
            raise RuntimeError("VadChunker requires soundfile to load audio") from exc

        data, sr = sf.read(str(audio_path), dtype="float32", always_2d=False)
        if data.ndim > 1:
            data = data.mean(axis=1).astype(np.float32, copy=False)
        if sr != _SAMPLE_RATE:
            try:
                import librosa
            except ImportError as exc:
                raise RuntimeError(
                    "VadChunker requires librosa to resample non-16 kHz audio"
                ) from exc
            data = librosa.resample(data, orig_sr=sr, target_sr=_SAMPLE_RATE)
            data = data.astype(np.float32, copy=False)
        return data


# ---------------------------------------------------------------------------
# Offline speech-timestamp scanner
#
# When the pre-baked ``silero_vad.onnx`` is all that's available (per
# ``docker/Dockerfile.base-nemo``) and the ``silero_vad`` pip package is
# not importable, we scan the whole file window-by-window through the
# shared :class:`SileroOnnxModel` and reduce the frame-level speech
# probabilities to speech regions with the same API shape that
# ``silero_vad.get_speech_timestamps`` provides: a list of
# ``{"start": sample, "end": sample}`` dicts.
# ---------------------------------------------------------------------------


_WINDOW_SAMPLES = WINDOW_SAMPLES_16K  # Silero v5 16kHz frame size


def _get_speech_timestamps_offline(
    audio: Any,
    model: Any,
    *,
    threshold: float = 0.5,
    sampling_rate: int = _SAMPLE_RATE,
    min_speech_duration_ms: int = 250,
    min_silence_duration_ms: int = 300,
    return_seconds: bool = False,
    **_: Any,
) -> list[dict[str, int]]:
    """Minimal stand-in for ``silero_vad.get_speech_timestamps``.

    Scans ``audio`` (torch tensor or numpy array) in 512-sample windows,
    queries ``model(window, sampling_rate)`` to get a speech probability
    per frame, and merges frames into speech regions subject to the
    duration/silence thresholds. Used only when the silero_vad pip
    package isn't importable and we're falling back to an onnxruntime
    session loaded from ``DALSTON_SILERO_VAD_ONNX``.
    """
    # Accept either a torch tensor or a numpy array.
    if hasattr(audio, "detach"):
        audio_np = audio.detach().cpu().numpy()
    else:
        audio_np = np.asarray(audio)
    if audio_np.ndim > 1:
        audio_np = audio_np.reshape(-1)
    audio_np = audio_np.astype(np.float32, copy=False)

    if hasattr(model, "reset_states"):
        model.reset_states()

    total = audio_np.size
    min_speech_samples = int(min_speech_duration_ms * sampling_rate / 1000)
    min_silence_samples = int(min_silence_duration_ms * sampling_rate / 1000)

    probs: list[float] = []
    for start in range(0, total - _WINDOW_SAMPLES + 1, _WINDOW_SAMPLES):
        window = audio_np[start : start + _WINDOW_SAMPLES]
        probs.append(model(window, sampling_rate))

    regions: list[dict[str, int]] = []
    in_speech = False
    region_start = 0
    silence_run = 0
    for i, p in enumerate(probs):
        frame_start = i * _WINDOW_SAMPLES
        if p >= threshold:
            if not in_speech:
                in_speech = True
                region_start = frame_start
            silence_run = 0
        elif in_speech:
            silence_run += _WINDOW_SAMPLES
            if silence_run >= min_silence_samples:
                region_end = frame_start - silence_run + _WINDOW_SAMPLES
                if region_end - region_start >= min_speech_samples:
                    regions.append({"start": region_start, "end": region_end})
                in_speech = False
                silence_run = 0

    if in_speech:
        region_end = len(probs) * _WINDOW_SAMPLES
        if region_end - region_start >= min_speech_samples:
            regions.append({"start": region_start, "end": region_end})

    if return_seconds:
        regions = [
            {"start": r["start"] / sampling_rate, "end": r["end"] / sampling_rate}
            for r in regions
        ]
    return regions
