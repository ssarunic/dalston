"""Shared inference core for ONNX Parakeet batch and realtime engines.

Extracts common model loading and transcription logic so that both the
batch engine (queue-based) and the realtime engine (WebSocket-based) can
share a single loaded model and inference path via ONNX Runtime.

This module owns the NeMoOnnxModelManager and provides a runtime-neutral
interface for transcription using the onnx-asr library.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import structlog

from dalston.engine_sdk.managers import NeMoOnnxModelManager

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Result types — runtime-neutral, no dependency on batch or RT SDK types
# ---------------------------------------------------------------------------


@dataclass
class OnnxWordResult:
    """A single word with timing from ONNX inference."""

    word: str
    start: float
    end: float
    confidence: float | None = None


@dataclass
class OnnxSegmentResult:
    """A transcription segment from ONNX inference."""

    start: float
    end: float
    text: str
    words: list[OnnxWordResult] = field(default_factory=list)


@dataclass
class OnnxTranscriptionResult:
    """Complete transcription result from a single ONNX inference call."""

    text: str = ""
    segments: list[OnnxSegmentResult] = field(default_factory=list)
    language: str = "en"
    language_probability: float = 1.0


# ---------------------------------------------------------------------------
# NemoOnnxCore — shared inference logic
# ---------------------------------------------------------------------------


class NemoOnnxCore:
    """Shared inference logic for ONNX Parakeet batch and realtime.

    Owns the NeMoOnnxModelManager and provides a unified transcription
    interface. Both batch and realtime adapters delegate inference here
    while keeping their own I/O contracts and output formatting.
    """

    SUPPORTED_MODELS = list(NeMoOnnxModelManager.SUPPORTED_MODELS.keys())

    def __init__(
        self,
        device: str = "cpu",
        quantization: str = "none",
        ttl_seconds: int = 3600,
        max_loaded: int = 2,
        preload: str | None = None,
    ) -> None:
        self._manager = NeMoOnnxModelManager(
            device=device,
            quantization=quantization,
            ttl_seconds=ttl_seconds,
            max_loaded=max_loaded,
            preload=preload,
        )
        self._device = device
        self._quantization = quantization

        logger.info(
            "nemo_onnx_core_init",
            device=device,
            quantization=quantization,
            ttl_seconds=ttl_seconds,
            max_loaded=max_loaded,
        )

    # -- Properties ----------------------------------------------------------

    @property
    def device(self) -> str:
        return self._device

    @property
    def quantization(self) -> str:
        return self._quantization

    @property
    def manager(self) -> NeMoOnnxModelManager:
        """Expose manager for stats, shutdown, and direct model access."""
        return self._manager

    # -- Core transcription --------------------------------------------------

    def transcribe(
        self,
        audio: str | np.ndarray,
        model_id: str,
    ) -> OnnxTranscriptionResult:
        """Run transcription on audio input.

        Works with both file paths (batch) and numpy arrays (realtime).

        Args:
            audio: File path string or numpy float32 array (mono, 16kHz)
            model_id: Model identifier (e.g. "parakeet-onnx-ctc-0.6b")

        Returns:
            OnnxTranscriptionResult with text, segments, and words.
        """
        model = self._manager.acquire(model_id)
        try:
            return self.transcribe_with_model(model, audio)
        finally:
            self._manager.release(model_id)

    def transcribe_with_model(
        self,
        model: Any,
        audio: str | np.ndarray,
    ) -> OnnxTranscriptionResult:
        """Run transcription with an already-acquired model.

        Args:
            model: An acquired onnx-asr model instance
            audio: File path string or numpy float32 array

        Returns:
            OnnxTranscriptionResult with text, segments, and words.
        """
        # Enable timestamps on the model
        ts_model = model.with_timestamps()

        # Prepare audio for onnx-asr
        if isinstance(audio, np.ndarray):
            if audio.dtype != np.float32:
                audio = audio.astype(np.float32)
            if audio.ndim > 1:
                audio = audio.squeeze()
            result = ts_model.recognize(audio, sample_rate=16000)
        else:
            result = ts_model.recognize(str(audio))

        return self._parse_result(result)

    # -- Result parsing ------------------------------------------------------

    def _parse_result(self, result: Any) -> OnnxTranscriptionResult:
        """Parse onnx-asr recognition result into neutral types.

        Handles TimestampedResult with .text, .tokens, .timestamps
        as well as simpler result formats with .words.

        Args:
            result: onnx-asr recognition result

        Returns:
            OnnxTranscriptionResult
        """
        if hasattr(result, "text"):
            text = str(result.text).strip()
        else:
            text = str(result).strip()

        if not text:
            return OnnxTranscriptionResult()

        all_words: list[OnnxWordResult] = []

        # Try structured word output first (from some onnx-asr versions)
        if hasattr(result, "words") and result.words:
            for w in result.words:
                word_text = str(w.word if hasattr(w, "word") else w.text).strip()
                if word_text:
                    all_words.append(
                        OnnxWordResult(
                            word=word_text,
                            start=round(float(w.start), 3),
                            end=round(float(w.end), 3),
                        )
                    )
        # Fall back to token-level timestamps
        elif hasattr(result, "tokens") and hasattr(result, "timestamps"):
            tokens = result.tokens
            timestamps = result.timestamps
            if tokens and timestamps and len(tokens) == len(timestamps):
                all_words = self._tokens_to_words(tokens, timestamps)

        # Build segments from words
        segments = self._words_to_segments(all_words, text)

        return OnnxTranscriptionResult(
            text=text,
            segments=segments,
            language="en",
            language_probability=1.0,
        )

    @staticmethod
    def _is_word_boundary(token_text: str) -> bool:
        """Check if a token marks a word boundary (SentencePiece)."""
        return token_text.startswith("\u2581") or token_text.startswith(" ")

    @staticmethod
    def _is_sentence_ending(word_text: str) -> bool:
        """Check if a word ends with sentence-ending punctuation."""
        return word_text.rstrip().endswith((".", "?", "!", "。", "？", "！"))

    def _tokens_to_words(
        self,
        tokens: list[str],
        timestamps: list[float],
    ) -> list[OnnxWordResult]:
        """Group subword tokens into words using SentencePiece boundaries."""
        if not tokens or not timestamps:
            return []

        words: list[OnnxWordResult] = []
        current_text_parts: list[str] = []
        current_start: float | None = None
        current_end: float = 0.0

        for i, token_text in enumerate(tokens):
            token_start = timestamps[i]
            token_end = timestamps[i + 1] if i + 1 < len(timestamps) else token_start

            if self._is_word_boundary(token_text) and current_text_parts:
                word_text = "".join(current_text_parts).replace("\u2581", "").strip()
                if word_text and current_start is not None:
                    words.append(
                        OnnxWordResult(
                            word=word_text,
                            start=round(current_start, 3),
                            end=round(current_end, 3),
                        )
                    )
                current_text_parts = [token_text]
                current_start = token_start
                current_end = token_end
            else:
                if current_start is None:
                    current_start = token_start
                current_text_parts.append(token_text)
                current_end = token_end

        # Flush last word
        if current_text_parts:
            word_text = "".join(current_text_parts).replace("\u2581", "").strip()
            if word_text and current_start is not None:
                words.append(
                    OnnxWordResult(
                        word=word_text,
                        start=round(current_start, 3),
                        end=round(current_end, 3),
                    )
                )

        return words

    def _words_to_segments(
        self, all_words: list[OnnxWordResult], full_text: str
    ) -> list[OnnxSegmentResult]:
        """Group words into segments based on sentence boundaries."""
        if not all_words:
            if full_text:
                return [OnnxSegmentResult(start=0.0, end=0.0, text=full_text)]
            return []

        segments: list[OnnxSegmentResult] = []
        current_words: list[OnnxWordResult] = []

        for word in all_words:
            current_words.append(word)
            if self._is_sentence_ending(word.word):
                seg_text = " ".join(w.word for w in current_words)
                segments.append(
                    OnnxSegmentResult(
                        start=current_words[0].start,
                        end=current_words[-1].end,
                        text=seg_text,
                        words=current_words.copy(),
                    )
                )
                current_words = []

        if current_words:
            seg_text = " ".join(w.word for w in current_words)
            segments.append(
                OnnxSegmentResult(
                    start=current_words[0].start,
                    end=current_words[-1].end,
                    text=seg_text,
                    words=current_words,
                )
            )

        return segments

    # -- Lifecycle -----------------------------------------------------------

    def get_stats(self) -> dict:
        """Get model manager statistics."""
        return self._manager.get_stats()

    def shutdown(self) -> None:
        """Shutdown core and release all models."""
        logger.info("nemo_onnx_core_shutdown")
        self._manager.shutdown()

    # -- Factory -------------------------------------------------------------

    @classmethod
    def from_env(cls) -> NemoOnnxCore:
        """Create a NemoOnnxCore configured from environment variables.

        Environment variables:
            DALSTON_DEVICE: Device ("cuda" or "cpu", default: auto-detect)
            DALSTON_QUANTIZATION: Quantization ("none" or "int8", default: none)
            DALSTON_MODEL_TTL_SECONDS: TTL in seconds (default: 3600)
            DALSTON_MAX_LOADED_MODELS: Max models (default: 2)
            DALSTON_MODEL_PRELOAD: Model to preload (optional)
        """
        device = os.environ.get("DALSTON_DEVICE", "").lower()
        if not device or device == "auto":
            try:
                import onnxruntime as ort

                if "CUDAExecutionProvider" in ort.get_available_providers():
                    device = "cuda"
                else:
                    device = "cpu"
            except ImportError:
                device = "cpu"

        quantization = os.environ.get("DALSTON_QUANTIZATION", "none").lower()

        return cls(
            device=device,
            quantization=quantization,
            ttl_seconds=int(os.environ.get("DALSTON_MODEL_TTL_SECONDS", "3600")),
            max_loaded=int(os.environ.get("DALSTON_MAX_LOADED_MODELS", "2")),
            preload=os.environ.get("DALSTON_MODEL_PRELOAD"),
        )
