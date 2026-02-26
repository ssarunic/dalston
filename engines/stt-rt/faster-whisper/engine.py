"""Real-time Whisper streaming transcription engine.

Uses faster-whisper for transcription with Silero VAD for speech detection.
Loads both distil-whisper and large-v3 model variants.
"""

from typing import Any

import numpy as np
import structlog
from faster_whisper import WhisperModel

from dalston.realtime_sdk import RealtimeEngine, TranscribeResult, Word

logger = structlog.get_logger()


class WhisperStreamingEngine(RealtimeEngine):
    """Real-time streaming transcription using Whisper.

    Loads models on startup and handles concurrent sessions for
    low-latency streaming transcription.

    Environment variables:
        WORKER_ID: Unique identifier for this worker (required)
        WORKER_PORT: WebSocket server port (default: 9000)
        MAX_SESSIONS: Maximum concurrent sessions (default: 4)
        REDIS_URL: Redis connection URL (default: redis://localhost:6379)
    """

    # Model configurations - use Systran CTranslate2-converted models for faster-whisper
    # Keys are canonical model names exposed to clients, values are HuggingFace model IDs
    MODELS = {
        "faster-whisper-distil-large-v3": "Systran/faster-distil-whisper-large-v3",
        "faster-whisper-large-v3": "Systran/faster-whisper-large-v3",
    }
    DEFAULT_MODEL = "faster-whisper-distil-large-v3"

    def __init__(self) -> None:
        """Initialize the engine."""
        super().__init__()
        self._models: dict[str, WhisperModel] = {}
        self._device: str = "cpu"
        self._compute_type: str = "int8"

    def load_models(self) -> None:
        """Load Whisper models.

        Automatically detects GPU availability and adjusts compute type.
        """
        # Detect device
        self._device, self._compute_type = self._detect_device()
        logger.info(
            "using_device", device=self._device, compute_type=self._compute_type
        )

        # Load all configured models
        for model_name, hf_model_id in self.MODELS.items():
            logger.info("loading_model", model_name=model_name, hf_model_id=hf_model_id)
            self._models[model_name] = WhisperModel(
                hf_model_id,
                device=self._device,
                compute_type=self._compute_type,
            )
            logger.info("model_loaded", model_name=model_name)

    def _detect_device(self) -> tuple[str, str]:
        """Detect the best available device and compute type.

        Returns:
            Tuple of (device, compute_type)
        """
        try:
            import torch

            if torch.cuda.is_available():
                return "cuda", "float16"
        except ImportError:
            pass

        # Fallback to CPU
        return "cpu", "int8"

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
            language: Language code (e.g., "en") or "auto" for detection
            model_variant: Model name (e.g., "faster-whisper-large-v3")
            vocabulary: List of terms to boost recognition (hotwords)

        Returns:
            TranscribeResult with text, words, language, confidence
        """
        # Select model
        model = self._models.get(model_variant)
        if model is None:
            # Fallback to default model
            model = self._models.get(self.DEFAULT_MODEL)
            if model is None:
                raise RuntimeError("No models loaded")

        # Handle language
        lang = None if language == "auto" else language

        # Build transcription kwargs
        transcribe_kwargs: dict = {
            "language": lang,
            "beam_size": 5,
            "vad_filter": False,  # We handle VAD separately
            "word_timestamps": True,
        }

        # Add vocabulary as initial_prompt if provided
        # initial_prompt is more effective than hotwords for biasing transcription
        if vocabulary:
            # Format as comma-separated list in the prompt
            transcribe_kwargs["initial_prompt"] = ", ".join(vocabulary)
            logger.debug(
                "vocabulary_enabled",
                terms=vocabulary[:5],
                total_terms=len(vocabulary),
            )

        # Transcribe
        segments, info = model.transcribe(
            audio,
            **transcribe_kwargs,
        )

        # Collect results
        words: list[Word] = []
        text_parts: list[str] = []

        for segment in segments:
            text_parts.append(segment.text.strip())

            # Extract word-level timestamps
            if segment.words:
                for word in segment.words:
                    words.append(
                        Word(
                            word=word.word.strip(),
                            start=word.start,
                            end=word.end,
                            confidence=word.probability,
                        )
                    )

        return TranscribeResult(
            text=" ".join(text_parts),
            words=words,
            language=info.language,
            confidence=info.language_probability,
        )

    def get_models(self) -> list[str]:
        """Return list of loaded model variants."""
        return list(self._models.keys())

    def get_languages(self) -> list[str]:
        """Return list of supported languages.

        Whisper supports 99 languages, we return "auto" to indicate
        all are supported with auto-detection.
        """
        return ["auto"]

    def get_engine(self) -> str:
        """Return engine type identifier."""
        return "whisper"

    def get_supports_vocabulary(self) -> bool:
        """Return True - faster-whisper supports vocabulary via hotwords."""
        return True

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
        """Return health status including model and GPU info."""
        base_health = super().health_check()

        # Add engine-specific info
        cuda_available = False
        cuda_device_count = 0

        try:
            import torch

            cuda_available = torch.cuda.is_available()
            cuda_device_count = torch.cuda.device_count() if cuda_available else 0
        except ImportError:
            pass

        return {
            **base_health,
            "models_loaded": list(self._models.keys()),
            "device": self._device,
            "compute_type": self._compute_type,
            "cuda_available": cuda_available,
            "cuda_device_count": cuda_device_count,
        }


if __name__ == "__main__":
    import asyncio

    engine = WhisperStreamingEngine()
    asyncio.run(engine.run())
