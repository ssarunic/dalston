"""Shared inference core for NeMo Parakeet batch and realtime engines.

Extracts common model loading and transcription logic so that both the
batch engine (queue-based) and the realtime engine (WebSocket-based) can
share a single loaded model and inference path.

This module owns the NeMoModelManager and provides a runtime-neutral
interface for transcription. Each engine adapter (batch / realtime) is
responsible for formatting the raw results into its own output contract.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import structlog

from dalston.engine_sdk.managers import NeMoModelManager

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Result types — runtime-neutral, no dependency on batch or RT SDK types
# ---------------------------------------------------------------------------


@dataclass
class NeMoWordResult:
    """A single word with timing from NeMo hypothesis."""

    word: str
    start: float
    end: float
    confidence: float | None = None


@dataclass
class NeMoSegmentResult:
    """A transcription segment from NeMo hypothesis."""

    start: float
    end: float
    text: str
    words: list[NeMoWordResult] = field(default_factory=list)


@dataclass
class NeMoTranscriptionResult:
    """Complete transcription result from a single NeMo inference call."""

    text: str = ""
    segments: list[NeMoSegmentResult] = field(default_factory=list)
    language: str = "en"
    language_probability: float = 1.0


# ---------------------------------------------------------------------------
# ParakeetCore — shared inference logic
# ---------------------------------------------------------------------------


class ParakeetCore:
    """Shared inference logic for NeMo Parakeet batch and realtime.

    Owns the NeMoModelManager and provides a unified transcription interface.
    Both batch and realtime adapters delegate inference here while keeping
    their own I/O contracts and output formatting.

    For advanced features like vocabulary boosting (GPU-PB) that require
    direct model access, adapters can use the ``manager`` property to
    acquire/release models and call ``transcribe_with_model()`` directly.
    """

    SUPPORTED_MODELS = list(NeMoModelManager.SUPPORTED_MODELS.keys())

    def __init__(
        self,
        device: str = "cuda",
        ttl_seconds: int = 3600,
        max_loaded: int = 2,
        preload: str | None = None,
    ) -> None:
        self._manager = NeMoModelManager(
            device=device,
            ttl_seconds=ttl_seconds,
            max_loaded=max_loaded,
            preload=preload,
        )

        logger.info(
            "parakeet_core_init",
            device=device,
            ttl_seconds=ttl_seconds,
            max_loaded=max_loaded,
        )

    # -- Properties ----------------------------------------------------------

    @property
    def device(self) -> str:
        return self._manager.device

    @property
    def manager(self) -> NeMoModelManager:
        """Expose manager for stats, shutdown, and direct model access."""
        return self._manager

    # -- Core transcription --------------------------------------------------

    def transcribe(
        self,
        audio: str | np.ndarray | list,
        model_id: str,
    ) -> NeMoTranscriptionResult:
        """Run transcription on audio input.

        Works with both file paths (batch) and numpy arrays (realtime).

        Args:
            audio: File path string, numpy float32 array, or list of either
            model_id: Model identifier (e.g. "parakeet-tdt-1.1b")

        Returns:
            NeMoTranscriptionResult with text, segments, and words.
        """
        model = self._manager.acquire(model_id)
        try:
            return self.transcribe_with_model(model, audio)
        finally:
            self._manager.release(model_id)

    def transcribe_with_model(
        self,
        model: Any,
        audio: str | np.ndarray | list,
    ) -> NeMoTranscriptionResult:
        """Run transcription with an already-acquired model.

        Use this when you need direct model access (e.g., for vocabulary
        boosting) and manage the acquire/release lifecycle yourself.

        Args:
            model: An acquired NeMo ASRModel instance
            audio: File path string, numpy float32 array, or list of either

        Returns:
            NeMoTranscriptionResult with text, segments, and words.
        """
        import torch

        # Normalize audio input to list format expected by NeMo
        if isinstance(audio, str | np.ndarray):
            audio_list = [audio]
        else:
            audio_list = audio

        # Prepare numpy arrays
        prepared = []
        for item in audio_list:
            if isinstance(item, np.ndarray):
                if item.dtype != np.float32:
                    item = item.astype(np.float32)
                if item.ndim > 1:
                    item = item.squeeze()
            prepared.append(item)

        # Run inference with appropriate context manager
        autocast_ctx = (
            torch.cuda.amp.autocast()
            if self.device == "cuda"
            else torch.inference_mode()
        )
        with autocast_ctx:
            transcriptions = model.transcribe(
                prepared,
                batch_size=1,
                return_hypotheses=True,
                timestamps=True,
            )

        if not transcriptions:
            return NeMoTranscriptionResult()

        # Handle NeMo API: transcriptions[batch][strategy] or transcriptions[batch]
        first_result = transcriptions[0]
        if isinstance(first_result, list):
            hypothesis = first_result[0]
        else:
            hypothesis = first_result

        full_text = hypothesis.text if hasattr(hypothesis, "text") else str(hypothesis)

        # Parse timestamps from hypothesis
        segments, all_words = self._parse_hypothesis(hypothesis, full_text)

        return NeMoTranscriptionResult(
            text=full_text.strip(),
            segments=segments,
            language="en",
            language_probability=1.0,
        )

    # -- Hypothesis parsing --------------------------------------------------

    @staticmethod
    def _parse_hypothesis(
        hypothesis: Any, full_text: str
    ) -> tuple[list[NeMoSegmentResult], list[NeMoWordResult]]:
        """Parse a NeMo hypothesis into segments and words.

        Handles three timestep formats:
        1. Dict with 'word'/'segment' keys (TDT models with timestamps=True)
        2. List of frame indices (RNNT legacy format)
        3. No timestep data (fallback)

        Args:
            hypothesis: NeMo Hypothesis object
            full_text: Full transcription text

        Returns:
            Tuple of (segments, all_words)
        """
        segments: list[NeMoSegmentResult] = []
        all_words: list[NeMoWordResult] = []

        # Case 1: TDT dict format
        if hasattr(hypothesis, "timestep") and isinstance(hypothesis.timestep, dict):
            word_timestamps = hypothesis.timestep.get("word", [])
            segment_timestamps = hypothesis.timestep.get("segment", [])

            for wt in word_timestamps:
                all_words.append(
                    NeMoWordResult(
                        word=wt.get("word", ""),
                        start=round(wt.get("start", 0.0), 3),
                        end=round(wt.get("end", 0.0), 3),
                    )
                )

            if segment_timestamps:
                for seg in segment_timestamps:
                    seg_start = seg.get("start", 0.0)
                    seg_end = seg.get("end", 0.0)
                    seg_text = seg.get("segment", "")
                    seg_words = [
                        w
                        for w in all_words
                        if w.start >= seg_start - 0.01 and w.end <= seg_end + 0.01
                    ]
                    segments.append(
                        NeMoSegmentResult(
                            start=round(seg_start, 3),
                            end=round(seg_end, 3),
                            text=seg_text,
                            words=seg_words if seg_words else [],
                        )
                    )
            elif all_words:
                segments.append(
                    NeMoSegmentResult(
                        start=all_words[0].start,
                        end=all_words[-1].end,
                        text=full_text.strip(),
                        words=all_words,
                    )
                )

        # Case 2: RNNT legacy list format
        elif hasattr(hypothesis, "timestep") and hypothesis.timestep is not None:
            timesteps = hypothesis.timestep
            tokens = full_text.split()
            frame_shift_seconds = 0.01

            for i, (token, frame_idx) in enumerate(
                zip(tokens, timesteps, strict=False)
            ):
                word_start = frame_idx * frame_shift_seconds
                if i + 1 < len(timesteps):
                    word_end = timesteps[i + 1] * frame_shift_seconds
                else:
                    word_end = word_start + 0.1

                all_words.append(
                    NeMoWordResult(
                        word=token,
                        start=round(word_start, 3),
                        end=round(word_end, 3),
                    )
                )

            if all_words:
                segments.append(
                    NeMoSegmentResult(
                        start=all_words[0].start,
                        end=all_words[-1].end,
                        text=full_text.strip(),
                        words=all_words,
                    )
                )

        # Case 3: No timestamp data
        else:
            if full_text.strip():
                segments.append(
                    NeMoSegmentResult(
                        start=0.0,
                        end=0.0,
                        text=full_text.strip(),
                    )
                )

        return segments, all_words

    # -- Lifecycle -----------------------------------------------------------

    def get_stats(self) -> dict:
        """Get model manager statistics."""
        return self._manager.get_stats()

    def shutdown(self) -> None:
        """Shutdown core and release all models."""
        logger.info("parakeet_core_shutdown")
        self._manager.shutdown()

    # -- Factory -------------------------------------------------------------

    @classmethod
    def from_env(cls) -> ParakeetCore:
        """Create a ParakeetCore configured from environment variables.

        Environment variables:
            DALSTON_DEVICE: Device ("cuda" or "cpu", default: auto-detect)
            DALSTON_MODEL_TTL_SECONDS: TTL in seconds (default: 3600)
            DALSTON_MAX_LOADED_MODELS: Max models (default: 2)
            DALSTON_MODEL_PRELOAD: Model to preload (optional)
        """
        # Auto-detect device
        device = os.environ.get("DALSTON_DEVICE", "").lower()
        if not device or device == "auto":
            try:
                import torch

                device = "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                device = "cpu"

        return cls(
            device=device,
            ttl_seconds=int(os.environ.get("DALSTON_MODEL_TTL_SECONDS", "3600")),
            max_loaded=int(os.environ.get("DALSTON_MAX_LOADED_MODELS", "2")),
            preload=os.environ.get("DALSTON_MODEL_PRELOAD"),
        )
