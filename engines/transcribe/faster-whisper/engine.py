"""Faster-Whisper transcription engine.

Uses the faster-whisper library (CTranslate2-based) for efficient
speech-to-text transcription with GPU acceleration.
"""

import logging
from pathlib import Path
from typing import Any

from faster_whisper import WhisperModel

from dalston.engine_sdk import Engine, TaskInput, TaskOutput

logger = logging.getLogger(__name__)


class FasterWhisperEngine(Engine):
    """Faster-Whisper transcription engine.

    Loads the Whisper model lazily on first request and caches it
    for subsequent transcriptions. Supports multiple model sizes
    and VAD filtering for improved accuracy.

    Automatically detects GPU availability and falls back to CPU mode.
    """

    # Default configuration
    DEFAULT_MODEL = "large-v3"
    DEFAULT_BEAM_SIZE = 5
    DEFAULT_VAD_FILTER = True

    def __init__(self) -> None:
        super().__init__()
        self._model: WhisperModel | None = None
        self._model_size: str | None = None

        # Auto-detect device and compute type
        self._device, self._compute_type = self._detect_device()
        logger.info(f"Detected device: {self._device}, compute_type: {self._compute_type}")

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

    def _load_model(self, model_size: str, device: str, compute_type: str) -> None:
        """Load the Whisper model if not already loaded.

        Args:
            model_size: Model size (tiny, base, small, medium, large-v2, large-v3)
            device: Device to use (cuda, cpu)
            compute_type: Compute type (float16, int8, int8_float16)
        """
        # Only reload if model size changed
        if self._model is not None and self._model_size == model_size:
            return

        logger.info(f"Loading Whisper model: {model_size} on {device} ({compute_type})")

        self._model = WhisperModel(
            model_size,
            device=device,
            compute_type=compute_type,
        )
        self._model_size = model_size

        logger.info(f"Model {model_size} loaded successfully")

    def process(self, input: TaskInput) -> TaskOutput:
        """Transcribe audio using Faster-Whisper.

        Args:
            input: Task input with audio file path and config

        Returns:
            TaskOutput with transcription text, segments, and language
        """
        audio_path = input.audio_path
        config = input.config

        # Get configuration with defaults (use auto-detected device settings)
        model_size = config.get("model", self.DEFAULT_MODEL)
        device = config.get("device", self._device)
        compute_type = config.get("compute_type", self._compute_type)
        # Handle language: None or "auto" means auto-detect
        language = config.get("language")
        if language == "auto" or language == "":
            language = None  # faster-whisper uses None for auto-detect
        beam_size = config.get("beam_size", self.DEFAULT_BEAM_SIZE)
        vad_filter = config.get("vad_filter", self.DEFAULT_VAD_FILTER)

        # Load model (lazy loading, cached)
        self._load_model(model_size, device, compute_type)

        logger.info(f"Transcribing: {audio_path}")
        logger.info(f"Config: model={model_size}, language={language}, beam_size={beam_size}, vad_filter={vad_filter}")

        # Transcribe audio
        segments_generator, info = self._model.transcribe(
            str(audio_path),
            language=language,
            beam_size=beam_size,
            vad_filter=vad_filter,
            word_timestamps=True,  # Enable word-level timestamps
        )

        # Collect segments
        segments = []
        full_text_parts = []

        for segment in segments_generator:
            segment_data: dict[str, Any] = {
                "start": round(segment.start, 3),
                "end": round(segment.end, 3),
                "text": segment.text.strip(),
            }

            # Add word-level timestamps if available
            if segment.words:
                segment_data["words"] = [
                    {
                        "word": word.word.strip(),
                        "start": round(word.start, 3),
                        "end": round(word.end, 3),
                        "probability": round(word.probability, 3),
                    }
                    for word in segment.words
                ]

            segments.append(segment_data)
            full_text_parts.append(segment.text.strip())

        # Build full text
        full_text = " ".join(full_text_parts)

        logger.info(f"Transcription complete: {len(segments)} segments, {len(full_text)} chars")
        logger.info(f"Detected language: {info.language} (probability: {info.language_probability:.2f})")

        return TaskOutput(
            data={
                "text": full_text,
                "segments": segments,
                "language": info.language,
                "language_probability": round(info.language_probability, 3),
            }
        )

    def health_check(self) -> dict[str, Any]:
        """Return health status including GPU availability."""
        import torch

        return {
            "status": "healthy",
            "model_loaded": self._model is not None,
            "model_size": self._model_size,
            "cuda_available": torch.cuda.is_available(),
            "cuda_device_count": torch.cuda.device_count() if torch.cuda.is_available() else 0,
        }


if __name__ == "__main__":
    engine = FasterWhisperEngine()
    engine.run()
