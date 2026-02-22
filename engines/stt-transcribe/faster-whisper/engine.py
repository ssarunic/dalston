"""Whisper transcription engine.

Uses the faster-whisper library (CTranslate2-based) for efficient
speech-to-text transcription with GPU acceleration.

This engine supports multiple model variants configured via the MODEL_VARIANT
environment variable. Each variant has its own engine.yaml in the variants/
directory with appropriate hardware requirements.
"""

import os
from typing import Any

from faster_whisper import WhisperModel

from dalston.engine_sdk import (
    AlignmentMethod,
    Engine,
    Segment,
    TaskInput,
    TaskOutput,
    TimestampGranularity,
    TranscribeOutput,
    Word,
)


class WhisperEngine(Engine):
    """Whisper transcription engine.

    Loads the Whisper model lazily on first request and caches it
    for subsequent transcriptions. The model variant is determined by
    the MODEL_VARIANT environment variable, set at container build time.

    Automatically detects GPU availability. GPU-only variants fail fast
    when CUDA is unavailable unless explicitly forced with DEVICE=cpu.
    """

    # Default configuration
    DEFAULT_BEAM_SIZE = 5
    DEFAULT_VAD_FILTER = True
    GPU_ONLY_VARIANTS = {"large-v3", "large-v3-turbo"}

    def __init__(self) -> None:
        super().__init__()
        self._model: WhisperModel | None = None

        # Model variant determined by container, not request config
        self._model_variant = os.environ.get("MODEL_VARIANT", "large-v3")

        # Auto-detect device and compute type
        self._device, self._compute_type = self._detect_device()
        self.logger.info(
            "engine_init",
            model_variant=self._model_variant,
            device=self._device,
            compute_type=self._compute_type,
        )

    def _detect_device(self) -> tuple[str, str]:
        """Detect the best available device and compute type.

        Returns:
            Tuple of (device, compute_type)
        """
        # Check for explicit device override
        requested_device = os.environ.get("DEVICE", "").lower()
        gpu_only_variant = self._model_variant in self.GPU_ONLY_VARIANTS
        if requested_device == "cpu":
            if gpu_only_variant:
                self.logger.warning(
                    "gpu_only_variant_forced_cpu",
                    model_variant=self._model_variant,
                    message=(
                        "GPU-only variant forced to CPU. "
                        "Use only for local development/testing."
                    ),
                )
            return "cpu", "int8"

        try:
            import torch

            cuda_available = torch.cuda.is_available()
            if cuda_available:
                return "cuda", "float16"
        except ImportError:
            cuda_available = False

        if requested_device == "cuda":
            raise RuntimeError(
                "DEVICE=cuda but CUDA is not available for faster-whisper."
            )

        if requested_device not in ("", "auto"):
            raise ValueError(
                f"Unknown DEVICE value: {requested_device}. Use cuda or cpu."
            )

        if gpu_only_variant:
            raise RuntimeError(
                f"Model variant '{self._model_variant}' requires CUDA, "
                "but CUDA is not available. Set DEVICE=cpu only for local "
                "development/testing."
            )

        # Fallback to CPU
        return "cpu", "int8"

    def _load_model(self) -> None:
        """Load the Whisper model if not already loaded."""
        if self._model is not None:
            return

        self.logger.info(
            "loading_whisper_model",
            model_variant=self._model_variant,
            device=self._device,
            compute_type=self._compute_type,
        )

        self._model = WhisperModel(
            self._model_variant,
            device=self._device,
            compute_type=self._compute_type,
        )

        self.logger.info("model_loaded_successfully", model_variant=self._model_variant)

    def process(self, input: TaskInput) -> TaskOutput:
        """Transcribe audio using Faster-Whisper.

        Args:
            input: Task input with audio file path and config

        Returns:
            TaskOutput with TranscribeOutput containing text, segments, and language
        """
        audio_path = input.audio_path
        config = input.config

        # Handle language: None or "auto" means auto-detect
        language = config.get("language")
        if language == "auto" or language == "":
            language = None  # faster-whisper uses None for auto-detect
        beam_size = config.get("beam_size", self.DEFAULT_BEAM_SIZE)
        vad_filter = config.get("vad_filter", self.DEFAULT_VAD_FILTER)
        channel = config.get("channel")  # For per_channel mode
        vocabulary = config.get("vocabulary")  # Terms to boost

        # Load model (lazy loading, cached)
        self._load_model()

        self.logger.info("transcribing", audio_path=str(audio_path))
        self.logger.info(
            "transcribe_config",
            model=self._model_variant,
            language=language,
            beam_size=beam_size,
            vad_filter=vad_filter,
            vocabulary_terms=len(vocabulary) if vocabulary else 0,
        )

        # Build transcribe kwargs
        transcribe_kwargs: dict = {
            "language": language,
            "beam_size": beam_size,
            "vad_filter": vad_filter,
            "word_timestamps": True,  # Enable word-level timestamps
        }

        # Add vocabulary as hotwords if provided
        # faster-whisper uses hotwords parameter to boost specific terms
        if vocabulary:
            transcribe_kwargs["hotwords"] = " ".join(vocabulary)
            self.logger.info(
                "vocabulary_enabled",
                terms=vocabulary[:5],  # Log first 5 terms
                total_terms=len(vocabulary),
            )

        # Transcribe audio
        segments_generator, info = self._model.transcribe(
            str(audio_path),
            **transcribe_kwargs,
        )

        # Collect segments
        segments: list[Segment] = []
        full_text_parts: list[str] = []

        for segment in segments_generator:
            # Build word list if available
            words: list[Word] | None = None
            if segment.words:
                words = [
                    Word(
                        text=word.word.strip(),
                        start=round(word.start, 3),
                        end=round(word.end, 3),
                        confidence=round(word.probability, 3),
                        alignment_method=AlignmentMethod.ATTENTION,
                    )
                    for word in segment.words
                ]

            segments.append(
                Segment(
                    start=round(segment.start, 3),
                    end=round(segment.end, 3),
                    text=segment.text.strip(),
                    words=words,
                )
            )
            full_text_parts.append(segment.text.strip())

        # Build full text
        full_text = " ".join(full_text_parts)

        self.logger.info(
            "transcription_complete",
            segment_count=len(segments),
            char_count=len(full_text),
        )
        self.logger.info(
            "detected_language",
            language=info.language,
            confidence=round(info.language_probability, 2),
        )

        # Determine actual granularity produced
        has_word_timestamps = any(seg.words for seg in segments)
        timestamp_granularity_actual = (
            TimestampGranularity.WORD
            if has_word_timestamps
            else TimestampGranularity.SEGMENT
        )

        # Get engine_id from environment or capabilities
        engine_id = os.environ.get("ENGINE_ID", f"whisper-{self._model_variant}")

        output = TranscribeOutput(
            text=full_text,
            segments=segments,
            language=info.language,
            language_confidence=round(info.language_probability, 3),
            duration=info.duration,
            timestamp_granularity_requested=TimestampGranularity.WORD,
            timestamp_granularity_actual=timestamp_granularity_actual,
            channel=channel,
            engine_id=engine_id,
            skipped=False,
            skip_reason=None,
            warnings=[],
        )

        return TaskOutput(data=output)

    def health_check(self) -> dict[str, Any]:
        """Return health status including GPU availability."""
        cuda_available = False
        cuda_device_count = 0

        try:
            import torch

            cuda_available = torch.cuda.is_available()
            cuda_device_count = torch.cuda.device_count() if cuda_available else 0
        except ImportError:
            pass

        return {
            "status": "healthy",
            "model_loaded": self._model is not None,
            "model_size": self._model_variant,
            "device": self._device,
            "compute_type": self._compute_type,
            "cuda_available": cuda_available,
            "cuda_device_count": cuda_device_count,
        }

    # Capabilities are loaded from engine.yaml by the base class.
    # word_timestamps is set to false because attention-based timestamps
    # are not as accurate as forced alignment (whisperx-align).


if __name__ == "__main__":
    engine = WhisperEngine()
    engine.run()
