"""Real-time Voxtral streaming transcription engine.

Uses Mistral Voxtral-Mini-4B-Realtime for low-latency multilingual
streaming transcription. Achieves <500ms latency with configurable
delay settings for accuracy/latency tradeoffs.

Environment variables:
    MODEL_VARIANT: Model variant to use (mini-4b). Defaults to mini-4b.
    DEVICE: Device to use for inference (cuda). GPU required for realtime.
    TRANSCRIPTION_DELAY_MS: Transcription delay in ms (80-2400). Default 480.
"""

import os
import re
from typing import Any

import numpy as np
import structlog
import torch

from dalston.realtime_sdk import RealtimeEngine, TranscribeResult, Word

logger = structlog.get_logger()


class VoxtralStreamingEngine(RealtimeEngine):
    """Real-time streaming transcription using Voxtral.

    Uses Voxtral-Mini-4B-Realtime for multilingual streaming transcription
    with configurable latency from 80ms to 2400ms.

    Environment variables:
        WORKER_ID: Unique identifier for this worker (required)
        WORKER_PORT: WebSocket server port (default: 9000)
        MAX_SESSIONS: Maximum concurrent sessions (default: 4)
        REDIS_URL: Redis connection URL (default: redis://localhost:6379)
        MODEL_VARIANT: Model variant (mini-4b). Defaults to mini-4b.
        TRANSCRIPTION_DELAY_MS: Delay in ms (default: 480, range: 80-2400)
        DEVICE: Device to use (cuda). GPU required for realtime performance.
    """

    MODEL_VARIANT_MAP = {
        "mini-4b": "mistralai/Voxtral-Mini-4B-Realtime-2602",
    }
    GPU_ONLY_VARIANTS = set(MODEL_VARIANT_MAP.keys())
    DEFAULT_MODEL_VARIANT = "mini-4b"
    DEFAULT_DELAY_MS = 480  # Sweet spot between latency and accuracy

    SUPPORTED_LANGUAGES = [
        "en",
        "zh",
        "hi",
        "es",
        "ar",
        "fr",
        "pt",
        "ru",
        "de",
        "ja",
        "ko",
        "it",
        "nl",
    ]

    def __init__(self) -> None:
        """Initialize the engine."""
        super().__init__()
        self._model = None
        self._processor = None
        self._model_name: str | None = None
        self._delay_ms: int = self.DEFAULT_DELAY_MS

        model_variant = os.environ.get(
            "DALSTON_MODEL_VARIANT", self.DEFAULT_MODEL_VARIANT
        )
        if model_variant not in self.MODEL_VARIANT_MAP:
            logger.warning(
                "unknown_model_variant",
                requested=model_variant,
                using=self.DEFAULT_MODEL_VARIANT,
            )
            model_variant = self.DEFAULT_MODEL_VARIANT
        self._model_variant = model_variant
        self._hf_model_id = self.MODEL_VARIANT_MAP[model_variant]

        requested_device = os.environ.get("DALSTON_DEVICE", "").lower()
        cuda_available = torch.cuda.is_available()
        gpu_only_variant = model_variant in self.GPU_ONLY_VARIANTS

        if requested_device == "cpu":
            self._device = "cpu"
            if gpu_only_variant:
                logger.warning(
                    "gpu_only_variant_forced_cpu",
                    variant=model_variant,
                    message=(
                        "GPU-optimized realtime variant forced to CPU. "
                        "Use only for development/testing."
                    ),
                )
            else:
                logger.warning(
                    "using_cpu_device",
                    message="Running on CPU - realtime latency will NOT be achievable",
                )
        elif requested_device in ("", "auto", "cuda"):
            if cuda_available:
                self._device = "cuda"
                logger.info("cuda_available", device_count=torch.cuda.device_count())
            else:
                if requested_device == "cuda" or gpu_only_variant:
                    raise RuntimeError(
                        f"Model variant '{model_variant}' requires CUDA, "
                        "but CUDA is not available. Set DEVICE=cpu only for "
                        "local development/testing."
                    )
                self._device = "cpu"
                logger.warning(
                    "cuda_not_available",
                    message="CUDA not available - realtime performance not possible",
                )
        else:
            raise ValueError(
                f"Unknown device: {requested_device}. Use 'cuda' or 'cpu'."
            )

    def load_models(self) -> None:
        """Load Voxtral model for streaming inference.

        Called once during engine startup.
        """
        self._delay_ms = int(
            os.environ.get("DALSTON_TRANSCRIPTION_DELAY_MS", str(self.DEFAULT_DELAY_MS))
        )
        self._delay_ms = max(80, min(2400, self._delay_ms))

        logger.info(
            "loading_voxtral_model",
            model_variant=self._model_variant,
            model_id=self._hf_model_id,
            delay_ms=self._delay_ms,
        )

        try:
            from transformers import (
                AutoProcessor,
                VoxtralRealtimeForConditionalGeneration,
            )
        except ImportError as e:
            raise RuntimeError(
                "Transformers not installed. Install with: pip install 'transformers>=5.2.0'"
            ) from e

        self._processor = AutoProcessor.from_pretrained(self._hf_model_id)

        torch_dtype = torch.bfloat16 if self._device == "cuda" else torch.float32

        self._model = VoxtralRealtimeForConditionalGeneration.from_pretrained(
            self._hf_model_id,
            torch_dtype=torch_dtype,
            device_map=self._device if self._device == "cuda" else None,
        )

        if self._device == "cpu":
            self._model = self._model.to(self._device)

        self._model.eval()
        self._model_name = self._hf_model_id

        logger.info("voxtral_model_loaded", model_id=self._hf_model_id)

    def _parse_streaming_output(self, text: str) -> tuple[str, list[Word]]:
        """Parse Voxtral realtime timestamp output.

        Voxtral Realtime outputs timestamps as: <|0.00|>word<|0.08|>
        Each token represents 80ms of audio.

        Args:
            text: Raw transcription output

        Returns:
            Tuple of (clean_text, words)
        """
        timestamp_pattern = r"<\|(\d+\.\d+)\|>"
        clean_text = re.sub(timestamp_pattern, "", text).strip()

        words: list[Word] = []
        parts = re.split(timestamp_pattern, text)

        current_time = 0.0
        for i, part in enumerate(parts):
            if not part.strip():
                continue

            try:
                current_time = float(part)
            except ValueError:
                word_text = part.strip()
                if word_text:
                    next_time = current_time + 0.08
                    for j in range(i + 1, len(parts)):
                        try:
                            next_time = float(parts[j])
                            break
                        except ValueError:
                            continue

                    words.append(
                        Word(
                            word=word_text,
                            start=round(current_time, 3),
                            end=round(next_time, 3),
                            confidence=0.95,
                        )
                    )

        return clean_text, words

    def transcribe(
        self,
        audio: np.ndarray,
        language: str,
        model_variant: str,
    ) -> TranscribeResult:
        """Transcribe an audio segment.

        Args:
            audio: Audio samples as float32 numpy array, mono, 16kHz
            language: Language code or "auto" for detection
            model_variant: Model variant (ignored - single model loaded)

        Returns:
            TranscribeResult with text, words, language, confidence
        """
        if self._model is None:
            raise RuntimeError("Model not loaded. Call load_models() first.")

        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)

        if audio.ndim > 1:
            audio = audio.squeeze()

        inputs = self._processor(
            audio=audio,
            sampling_rate=16000,
            return_tensors="pt",
        )

        if self._device == "cuda":
            inputs = {k: v.to(self._device) for k, v in inputs.items()}

        with torch.inference_mode():
            generated_ids = self._model.generate(
                **inputs,
                max_new_tokens=1024,
                return_timestamps=True,
            )

        transcription = self._processor.batch_decode(
            generated_ids,
            skip_special_tokens=False,
        )[0]

        clean_text, words = self._parse_streaming_output(transcription)

        detected_language = language if language != "auto" else "en"
        if detected_language not in self.SUPPORTED_LANGUAGES:
            detected_language = "en"

        return TranscribeResult(
            text=clean_text,
            words=words,
            language=detected_language,
            confidence=0.95,
        )

    def supports_streaming(self) -> bool:
        """Voxtral Realtime supports native streaming."""
        return True

    def get_models(self) -> list[str]:
        """Return list of supported model identifiers."""
        return [f"voxtral-{self._model_variant}"]

    def get_languages(self) -> list[str]:
        """Return list of supported languages."""
        return self.SUPPORTED_LANGUAGES

    def get_engine(self) -> str:
        """Return engine type identifier."""
        return f"voxtral-{self._model_variant}"

    def get_gpu_memory_usage(self) -> str:
        """Return GPU memory usage string."""
        if torch.cuda.is_available():
            used = torch.cuda.memory_allocated() / 1e9
            return f"{used:.1f}GB"
        return "0GB"

    def health_check(self) -> dict[str, Any]:
        """Return health status including model and GPU info."""
        base_health = super().health_check()

        cuda_available = torch.cuda.is_available()
        cuda_device_count = torch.cuda.device_count() if cuda_available else 0
        cuda_memory_allocated = 0
        cuda_memory_total = 0

        if cuda_available and cuda_device_count > 0:
            cuda_memory_allocated = torch.cuda.memory_allocated() / 1e9
            cuda_memory_total = torch.cuda.get_device_properties(0).total_memory / 1e9

        return {
            **base_health,
            "model_loaded": self._model is not None,
            "model_name": self._model_name,
            "delay_ms": self._delay_ms,
            "device": self._device,
            "cuda_available": cuda_available,
            "cuda_device_count": cuda_device_count,
            "cuda_memory_allocated_gb": round(cuda_memory_allocated, 2),
            "cuda_memory_total_gb": round(cuda_memory_total, 2),
        }


if __name__ == "__main__":
    import asyncio

    engine = VoxtralStreamingEngine()
    asyncio.run(engine.run())
