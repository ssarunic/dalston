"""Shared inference core for ONNX batch and realtime engines.

Extracts common model loading and transcription logic so that both the
batch engine (queue-based) and the realtime engine (WebSocket-based) can
share a single loaded model and inference path via ONNX Runtime.

This module owns the OnnxModelManager and provides an engine_id-neutral
interface for transcription using the onnx-asr library.

Long audio is automatically segmented using Silero VAD so that no single
inference call exceeds GPU memory limits.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import structlog

from dalston.engine_sdk.managers import OnnxModelManager

logger = structlog.get_logger()

# Default VAD settings for long-audio segmentation.
# max_speech_duration_s controls peak VRAM per inference call — Silero VAD
# splits at the nearest silence boundary before this limit. Safe defaults:
#   60s for 16GB (T4), 120s for 24GB+ (A10/L4/A100).
# Override via DALSTON_VAD_MAX_SPEECH_S env var.
_DEFAULT_MAX_SPEECH_DURATION_S = 60.0
_DEFAULT_MIN_SILENCE_DURATION_MS = 400.0
_DEFAULT_VAD_BATCH_SIZE = 8


# ---------------------------------------------------------------------------
# Result types — engine_id-neutral, no dependency on batch or RT SDK types
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
# OnnxInference — shared inference logic
# ---------------------------------------------------------------------------


class OnnxInference:
    """Shared inference logic for ONNX batch and realtime engines.

    Owns the OnnxModelManager and provides a unified transcription
    interface. Both batch and realtime adapters delegate inference here
    while keeping their own I/O contracts and output formatting.
    """

    # Curated model aliases advertised at registration time.
    # The underlying manager accepts any onnx-asr compatible model ID.
    CURATED_MODELS = list(OnnxModelManager.MODEL_ALIASES.keys())

    def __init__(
        self,
        device: str = "cpu",
        quantization: str = "none",
        ttl_seconds: int = 3600,
        max_loaded: int = 2,
        preload: str | None = None,
    ) -> None:
        self._manager = OnnxModelManager(
            device=device,
            quantization=quantization,
            ttl_seconds=ttl_seconds,
            max_loaded=max_loaded,
            preload=preload,
        )
        self._device = device
        self._quantization = quantization
        self._vad: Any | None = None  # Lazy-loaded Silero VAD

        logger.info(
            "onnx_inference_init",
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
    def manager(self) -> OnnxModelManager:
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

        For file paths (batch mode), uses Silero VAD to segment long audio
        before transcription, preventing GPU OOM on large files. For numpy
        arrays (realtime mode), transcribes directly since chunks are short.

        Args:
            model: An acquired onnx-asr model instance
            audio: File path string or numpy float32 array

        Returns:
            OnnxTranscriptionResult with text, segments, and words.
        """
        if isinstance(audio, np.ndarray):
            return self._transcribe_direct(model, audio)
        return self._transcribe_with_vad(model, str(audio))

    def _transcribe_direct(
        self,
        model: Any,
        audio: np.ndarray,
    ) -> OnnxTranscriptionResult:
        """Transcribe a numpy array directly (no VAD, for short realtime chunks)."""
        ts_model = model.with_timestamps()
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)
        if audio.ndim > 1:
            audio = audio.squeeze()
        result = ts_model.recognize(audio, sample_rate=16000)
        return self._parse_result(result)

    def _transcribe_with_vad(
        self,
        model: Any,
        audio_path: str,
    ) -> OnnxTranscriptionResult:
        """Transcribe a file using VAD segmentation for long audio safety.

        Silero VAD splits audio at speech boundaries (max 60s per segment by default),
        then each segment is transcribed independently. This prevents GPU
        OOM on long recordings while producing properly-timed output.
        """
        vad = self._get_or_load_vad()

        max_speech_s = float(
            os.environ.get("DALSTON_VAD_MAX_SPEECH_S", _DEFAULT_MAX_SPEECH_DURATION_S)
        )
        vad_batch_size = int(
            os.environ.get("DALSTON_VAD_BATCH_SIZE", _DEFAULT_VAD_BATCH_SIZE)
        )

        # Chain: model → VAD segmentation → timestamped recognition
        vad_ts_model = model.with_vad(
            vad,
            max_speech_duration_s=max_speech_s,
            min_silence_duration_ms=_DEFAULT_MIN_SILENCE_DURATION_MS,
            batch_size=vad_batch_size,
        ).with_timestamps()

        # recognize() returns Iterator[TimestampedSegmentResult]
        vad_segments = vad_ts_model.recognize(audio_path)

        return self._parse_vad_result(vad_segments)

    def _get_or_load_vad(self) -> Any:
        """Lazy-load Silero VAD model."""
        if self._vad is None:
            try:
                from onnx_asr import load_vad
            except ImportError as e:
                raise ImportError(
                    "onnx-asr VAD support not available. "
                    "Install with: pip install onnx-asr[hub]"
                ) from e

            logger.info("loading_silero_vad")
            self._vad = load_vad("silero")
            logger.info("silero_vad_loaded")
        return self._vad

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

    def _parse_vad_result(self, vad_segments: Any) -> OnnxTranscriptionResult:
        """Parse VAD-segmented recognition results into neutral types.

        Each VAD segment is a TimestampedSegmentResult with:
        - start/end: absolute position in the full audio (seconds)
        - text: recognized text for this segment
        - timestamps/tokens: token-level timing relative to segment start

        Token timestamps are offset by segment.start to produce absolute times.
        """
        all_segments: list[OnnxSegmentResult] = []
        all_text_parts: list[str] = []

        for seg in vad_segments:
            seg_text = str(seg.text).strip()
            if not seg_text:
                continue

            all_text_parts.append(seg_text)
            seg_start = float(seg.start)
            seg_end = float(seg.end)

            # Parse words from token-level timestamps (offset to absolute time)
            seg_words: list[OnnxWordResult] = []
            if (
                hasattr(seg, "tokens")
                and hasattr(seg, "timestamps")
                and seg.tokens
                and seg.timestamps
            ):
                raw_words = self._tokens_to_words(seg.tokens, seg.timestamps)
                for w in raw_words:
                    seg_words.append(
                        OnnxWordResult(
                            word=w.word,
                            start=round(w.start + seg_start, 3),
                            end=round(w.end + seg_start, 3),
                            confidence=w.confidence,
                        )
                    )

            all_segments.append(
                OnnxSegmentResult(
                    start=round(seg_start, 3),
                    end=round(seg_end, 3),
                    text=seg_text,
                    words=seg_words,
                )
            )

        full_text = " ".join(all_text_parts)

        logger.info(
            "vad_transcription_parsed",
            segment_count=len(all_segments),
            word_count=sum(len(s.words) for s in all_segments),
            char_count=len(full_text),
        )

        return OnnxTranscriptionResult(
            text=full_text,
            segments=all_segments,
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
        logger.info("onnx_inference_shutdown")
        self._vad = None
        self._manager.shutdown()

    # -- Factory -------------------------------------------------------------

    @classmethod
    def from_env(cls) -> OnnxInference:
        """Create an OnnxInference configured from environment variables.

        Environment variables:
            DALSTON_DEVICE: Device ("cuda" or "cpu", default: auto-detect)
            DALSTON_QUANTIZATION: Quantization ("none" or "int8", default: none)
            DALSTON_MODEL_TTL_SECONDS: TTL in seconds (default: 3600)
            DALSTON_MAX_LOADED_MODELS: Max models (default: 2)
            DALSTON_MODEL_PRELOAD: Model to preload (optional)
            DALSTON_VAD_MAX_SPEECH_S: Max speech segment duration in seconds (default: 60)
            DALSTON_VAD_BATCH_SIZE: Number of VAD segments per inference batch (default: 8)
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
