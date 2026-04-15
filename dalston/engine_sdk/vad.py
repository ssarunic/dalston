"""VAD-based audio chunker for long-audio transcription.

Wraps Silero VAD (loaded via torch.hub or the ``silero_vad`` pip package)
and exposes a chunk-oriented API to :class:`BaseBatchTranscribeEngine`
so any engine can opt into long-audio chunking by overriding
``get_max_audio_duration_s()``.

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

Environment variables:

- ``DALSTON_SILERO_VAD_PATH``: local path to a prebuilt silero-vad model
  (JIT-compiled ``.pt`` or a torch.hub-style directory). When set, the
  chunker loads from disk and skips the torch.hub download path.
"""

from __future__ import annotations

import os
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import structlog

logger = structlog.get_logger()


_SAMPLE_RATE = 16000


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

    Lazy-loads the Silero VAD model on first use. Thread-safe: concurrent
    ``split()`` calls share the same loaded model via a double-checked
    lock.

    Invariant: every returned chunk has ``duration <= max_chunk_duration_s``.
    When a single speech span exceeds the limit (no internal silence),
    the chunker force-splits at the boundary and logs
    ``vad_force_split`` with the span duration.
    """

    def __init__(
        self,
        max_chunk_duration_s: float = 1500.0,
        min_speech_duration_s: float = 0.25,
        min_silence_duration_s: float = 0.3,
        vad_threshold: float = 0.5,
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
        self._load_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Model loading
    # ------------------------------------------------------------------

    def _ensure_model(self) -> None:
        """Lazy-load the Silero VAD model on first use."""
        if self._model is not None:
            return

        with self._load_lock:
            if self._model is not None:
                return

            try:
                import torch
            except ImportError as exc:
                raise RuntimeError(
                    "VadChunker requires torch. Install torch in the engine image."
                ) from exc

            override_path = os.environ.get("DALSTON_SILERO_VAD_PATH")
            if override_path:
                model, utils = self._load_from_path(torch, override_path)
            else:
                try:
                    model, utils = torch.hub.load(
                        repo_or_dir="snakers4/silero-vad",
                        model="silero_vad",
                        force_reload=False,
                        onnx=False,
                        trust_repo=True,
                    )
                except Exception as hub_exc:
                    try:
                        from silero_vad import (
                            get_speech_timestamps,
                            load_silero_vad,
                        )
                    except ImportError as pkg_exc:
                        raise RuntimeError(
                            "Failed to load Silero VAD from torch.hub and the "
                            "'silero_vad' package is not installed. Install it "
                            "with: pip install silero-vad"
                        ) from pkg_exc
                    model = load_silero_vad()
                    self._model = model
                    self._get_speech_timestamps = get_speech_timestamps
                    logger.info(
                        "silero_vad_loaded",
                        backend="silero_vad_pkg",
                        hub_error=str(hub_exc)[:200],
                    )
                    return

            self._model = model
            self._get_speech_timestamps = utils[0]
            logger.info("silero_vad_loaded", backend="torch_hub")

    def _load_from_path(self, torch: Any, path: str) -> tuple[Any, Any]:
        """Load a cached Silero VAD snapshot from disk (env override)."""
        p = Path(path)
        if not p.exists():
            raise RuntimeError(f"DALSTON_SILERO_VAD_PATH={path} does not exist")

        if p.is_file():
            model = torch.jit.load(str(p), map_location="cpu")
            from silero_vad import get_speech_timestamps

            return model, (get_speech_timestamps,)

        model, utils = torch.hub.load(
            repo_or_dir=str(p),
            source="local",
            model="silero_vad",
            onnx=False,
            trust_repo=True,
        )
        return model, utils

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
        self._ensure_model()
        audio = self._load_audio_f32_mono_16k(audio_path)
        if audio.size == 0:
            return []

        import torch

        audio_tensor = torch.from_numpy(audio)
        assert self._get_speech_timestamps is not None
        raw_segments = self._get_speech_timestamps(
            audio_tensor,
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
        return segments

    # ------------------------------------------------------------------
    # Chunking
    # ------------------------------------------------------------------

    def split(
        self,
        audio_path: Path,
        temp_dir: Path,
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

        Returns:
            List of :class:`AudioChunk` in temporal order. Empty list
            if the source has no speech.
        """
        temp_dir.mkdir(parents=True, exist_ok=True)
        segments = self.detect_speech(audio_path)
        if not segments:
            logger.info("vad_no_speech_detected", audio_path=str(audio_path))
            return []

        audio_full = self._load_audio_f32_mono_16k(audio_path)
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
            start_sample = max(0, int(round(start_s * _SAMPLE_RATE)))
            end_sample = min(total_samples, int(round(end_s * _SAMPLE_RATE)))
            if end_sample <= start_sample:
                continue
            slice_ = audio_full[start_sample:end_sample]
            out_path = temp_dir / f"chunk_{idx:04d}.wav"
            self._write_wav(out_path, slice_)
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

    @staticmethod
    def _write_wav(path: Path, audio: np.ndarray) -> None:
        """Write a float32 mono array as 16-bit PCM WAV at 16 kHz."""
        pcm = np.clip(audio, -1.0, 1.0)
        pcm = (pcm * 32767.0).astype(np.int16)
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(_SAMPLE_RATE)
            wf.writeframes(pcm.tobytes())
