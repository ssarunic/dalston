"""Faster-Whisper transcription engine.

Uses the faster-whisper library (CTranslate2-based) for efficient
speech-to-text transcription with GPU acceleration.
"""

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
        self.logger.info(
            "detected_device", device=self._device, compute_type=self._compute_type
        )

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

        self.logger.info(
            "loading_whisper_model",
            model_size=model_size,
            device=device,
            compute_type=compute_type,
        )

        self._model = WhisperModel(
            model_size,
            device=device,
            compute_type=compute_type,
        )
        self._model_size = model_size

        self.logger.info("model_loaded_successfully", model_size=model_size)

    def process(self, input: TaskInput) -> TaskOutput:
        """Transcribe audio using Faster-Whisper.

        Args:
            input: Task input with audio file path and config

        Returns:
            TaskOutput with TranscribeOutput containing text, segments, and language
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
        channel = config.get("channel")  # For per_channel mode

        # Load model (lazy loading, cached)
        self._load_model(model_size, device, compute_type)

        self.logger.info("transcribing", audio_path=str(audio_path))
        self.logger.info(
            "transcribe_config",
            model=model_size,
            language=language,
            beam_size=beam_size,
            vad_filter=vad_filter,
        )

        # Transcribe audio
        segments_generator, info = self._model.transcribe(
            str(audio_path),
            language=language,
            beam_size=beam_size,
            vad_filter=vad_filter,
            word_timestamps=True,  # Enable word-level timestamps
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

        output = TranscribeOutput(
            text=full_text,
            segments=segments,
            language=info.language,
            language_confidence=round(info.language_probability, 3),
            duration=info.duration,
            timestamp_granularity_requested=TimestampGranularity.WORD,
            timestamp_granularity_actual=timestamp_granularity_actual,
            channel=channel,
            engine_id="faster-whisper",
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
            "model_size": self._model_size,
            "device": self._device,
            "compute_type": self._compute_type,
            "cuda_available": cuda_available,
            "cuda_device_count": cuda_device_count,
        }

    # Capabilities are loaded from engine.yaml by the base class.
    # word_timestamps is set to false because attention-based timestamps
    # are not as accurate as forced alignment (whisperx-align).


if __name__ == "__main__":
    engine = FasterWhisperEngine()
    engine.run()
