"""Real-time Parakeet ONNX streaming transcription engine.

Uses ONNX Runtime via the onnx-asr library for low-latency transcription
of VAD-segmented utterances. The SessionHandler provides VAD-based chunking,
and this engine transcribes each utterance using Parakeet CTC via ONNX.

Unlike the NeMo RNNT-based real-time engine, this uses CTC models which
don't support native streaming but work well with VAD-chunked audio.
The tradeoff is simpler deployment (no NeMo/PyTorch) at the cost of
slightly higher per-utterance latency.

Environment variables:
    DALSTON_MODEL_VARIANT: Model variant (ctc-0.6b, ctc-1.1b). Defaults to ctc-0.6b.
    DALSTON_DEVICE: Device to use for inference (cuda, cpu). Defaults to cpu.
    DALSTON_QUANTIZATION: ONNX quantization level (none, int8). Defaults to none.
"""

import os
from typing import Any

import numpy as np
import structlog

from dalston.realtime_sdk import RealtimeEngine, TranscribeResult, Word

logger = structlog.get_logger()

# Model variant to onnx-asr model name mapping
_VARIANT_TO_ONNX_ASR = {
    "ctc-0.6b": "nemo-parakeet-ctc-0.6b",
    "ctc-1.1b": "nemo-parakeet-ctc-1.1b",
}


class ParakeetOnnxStreamingEngine(RealtimeEngine):
    """Real-time transcription using Parakeet CTC via ONNX Runtime.

    Uses VAD-chunked transcription (not native streaming). The SDK's
    SessionHandler accumulates audio until a speech endpoint, then
    calls transcribe() on the accumulated chunk.

    Environment variables:
        DALSTON_WORKER_ID: Unique identifier for this worker (required)
        DALSTON_WORKER_PORT: WebSocket server port (default: 9000)
        DALSTON_MAX_SESSIONS: Maximum concurrent sessions (default: 4)
        REDIS_URL: Redis connection URL (default: redis://localhost:6379)
        DALSTON_MODEL_VARIANT: Model variant (ctc-0.6b, ctc-1.1b). Defaults to ctc-0.6b.
        DALSTON_DEVICE: Device to use (cuda, cpu). Defaults to cpu.
        DALSTON_QUANTIZATION: ONNX quantization level (none, int8). Defaults to none.
    """

    DEFAULT_MODEL_VARIANT = "ctc-0.6b"

    def __init__(self) -> None:
        """Initialize the engine."""
        super().__init__()
        self._model = None
        self._model_name: str | None = None

        # Determine model variant
        model_variant = os.environ.get(
            "DALSTON_MODEL_VARIANT", self.DEFAULT_MODEL_VARIANT
        )
        if model_variant not in _VARIANT_TO_ONNX_ASR:
            logger.warning(
                "unknown_model_variant",
                requested=model_variant,
                using=self.DEFAULT_MODEL_VARIANT,
            )
            model_variant = self.DEFAULT_MODEL_VARIANT
        self._model_variant = model_variant
        self._onnx_asr_name = _VARIANT_TO_ONNX_ASR[model_variant]

        # Device configuration
        requested_device = os.environ.get("DALSTON_DEVICE", "").lower()
        self._providers: list[str | tuple[str, dict]] = []

        if requested_device == "cuda":
            self._device = "cuda"
            self._providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        elif requested_device in ("", "auto", "cpu"):
            self._device = "cpu"
            self._providers = ["CPUExecutionProvider"]
            if requested_device in ("", "auto"):
                try:
                    import onnxruntime as ort

                    if "CUDAExecutionProvider" in ort.get_available_providers():
                        self._providers = [
                            "CUDAExecutionProvider",
                            "CPUExecutionProvider",
                        ]
                        self._device = "cuda"
                except ImportError:
                    pass
        else:
            raise ValueError(
                f"Unknown device: {requested_device}. Use 'cuda' or 'cpu'."
            )

        # Quantization
        self._quantization = os.environ.get("DALSTON_QUANTIZATION", "none").lower()

    def load_models(self) -> None:
        """Load Parakeet ONNX model for inference.

        Called once during engine startup.
        """
        logger.info(
            "loading_parakeet_onnx_model",
            model_variant=self._model_variant,
            onnx_asr_name=self._onnx_asr_name,
            device=self._device,
            quantization=self._quantization,
        )

        try:
            import onnx_asr
        except ImportError as e:
            raise RuntimeError(
                "onnx-asr not installed. Install with: pip install onnx-asr[cpu,hub]"
            ) from e

        quantization = self._quantization if self._quantization != "none" else None
        kwargs: dict[str, Any] = {}
        if self._providers:
            kwargs["providers"] = self._providers

        self._model = onnx_asr.load_model(
            self._onnx_asr_name,
            quantization=quantization,
            **kwargs,
        )
        self._model_name = self._onnx_asr_name

        logger.info(
            "parakeet_onnx_model_loaded",
            model_name=self._onnx_asr_name,
            device=self._device,
        )

    def transcribe(
        self,
        audio: np.ndarray,
        language: str,
        model_variant: str,
        vocabulary: list[str] | None = None,
    ) -> TranscribeResult:
        """Transcribe an audio segment.

        Args:
            audio: Audio samples as float32 numpy array, mono, 16kHz
            language: Language code (ignored - Parakeet is English-only)
            model_variant: Model variant (ignored - single model loaded)
            vocabulary: List of terms to boost recognition (not supported for ONNX)

        Returns:
            TranscribeResult with text, words, language, confidence
        """
        if self._model is None:
            raise RuntimeError("Model not loaded. Call load_models() first.")

        if vocabulary:
            logger.debug(
                "vocabulary_not_supported_onnx",
                message="Vocabulary boosting not supported for ONNX engine. Terms ignored.",
                terms_count=len(vocabulary),
            )

        if language != "auto" and language != "en":
            logger.warning(
                "language_not_supported",
                requested=language,
                using="en",
            )

        # Prepare audio for onnx-asr (expects float32 numpy array)
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)
        if audio.ndim > 1:
            audio = audio.squeeze()

        # Transcribe using onnx-asr
        result = self._model.recognize(audio, sample_rate=16000)

        # Extract text
        if hasattr(result, "text"):
            text = str(result.text).strip()
        else:
            text = str(result).strip()

        if not text:
            return TranscribeResult(
                text="",
                words=[],
                language="en",
                confidence=1.0,
            )

        # Extract word timestamps if available
        words: list[Word] = []
        if hasattr(result, "words") and result.words:
            for w in result.words:
                word_text = str(w.word if hasattr(w, "word") else w.text).strip()
                if word_text:
                    words.append(
                        Word(
                            word=word_text,
                            start=float(w.start),
                            end=float(w.end),
                            confidence=0.95,
                        )
                    )

        return TranscribeResult(
            text=text,
            words=words,
            language="en",
            confidence=1.0,
        )

    def supports_streaming(self) -> bool:
        """CTC models don't support native streaming."""
        return False

    def get_models(self) -> list[str]:
        """Return list of supported model identifiers."""
        return [f"parakeet-onnx-{self._model_variant}"]

    def get_languages(self) -> list[str]:
        """Return list of supported languages."""
        return ["en"]

    def get_engine(self) -> str:
        """Return engine type identifier."""
        return f"parakeet-onnx-{self._model_variant}"

    def get_gpu_memory_usage(self) -> str:
        """Return GPU memory usage string."""
        try:
            import torch

            if torch.cuda.is_available():
                used = torch.cuda.memory_allocated() / 1e9
                return f"{used:.1f}GB"
        except ImportError:
            pass
        return "0GB"

    def health_check(self) -> dict[str, Any]:
        """Return health status including model and device info."""
        base_health = super().health_check()
        return {
            **base_health,
            "model_loaded": self._model is not None,
            "model_name": self._model_name,
            "device": self._device,
            "quantization": self._quantization,
        }


if __name__ == "__main__":
    import asyncio

    engine = ParakeetOnnxStreamingEngine()
    asyncio.run(engine.run())
