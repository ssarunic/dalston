"""Faster-Whisper transcription engine with runtime model swapping.

Uses the faster-whisper library (CTranslate2-based) for efficient
speech-to-text transcription with GPU acceleration.

Phase 1 (Runtime Model Management):
    This engine now supports loading any Whisper model variant at runtime.
    The model to load is specified via config["runtime_model_id"] in the task,
    falling back to DALSTON_DEFAULT_MODEL_ID environment variable.

    Models are stored in /models/faster-whisper/ subdirectory (set via
    WHISPER_MODELS_DIR environment variable) to avoid cross-contamination
    with other runtimes on the shared volume.

Environment variables:
    DALSTON_ENGINE_ID: Runtime engine ID for registration (default: "faster-whisper")
    DALSTON_DEFAULT_MODEL_ID: Default model ID (default: "large-v3-turbo")
    DALSTON_DEVICE: Device to use for inference (cuda, cpu). Defaults to cuda if available.
    WHISPER_MODELS_DIR: Directory for model cache (default: /models/faster-whisper)
"""

import gc
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
    """Faster-Whisper transcription engine with runtime model swapping.

    Phase 1 (Runtime Model Management):
        This engine can load any Whisper model variant at runtime. The model
        is specified via config["runtime_model_id"] in the task payload. If a
        different model is requested, the current model is unloaded and the
        new one is loaded.

    Automatically detects GPU availability and selects appropriate compute type:
    - GPU (CUDA): float16 for maximum performance
    - CPU: int8 for efficient inference (all models including large-v3-turbo)
    """

    # Default configuration
    DEFAULT_BEAM_SIZE = 5
    DEFAULT_VAD_FILTER = True

    # Valid model identifiers that this runtime can load
    # These are the runtime_model_id values passed to WhisperModel()
    SUPPORTED_MODELS = {
        "tiny",
        "base",
        "small",
        "medium",
        "large-v2",
        "large-v3",
        "large-v3-turbo",
    }

    # Default model: multilingual, CPU-capable for "just works" experience
    DEFAULT_MODEL_ID = "large-v3-turbo"

    def __init__(self) -> None:
        super().__init__()
        self._model: WhisperModel | None = None
        self._loaded_model_id: str | None = None

        # Get default model from environment, with fallback to class default
        self._default_model_id = os.environ.get(
            "DALSTON_DEFAULT_MODEL_ID", self.DEFAULT_MODEL_ID
        )

        # Get engine ID from environment for registration (runtime ID, not variant ID)
        self._engine_id = os.environ.get("DALSTON_ENGINE_ID", "faster-whisper")

        # Get model cache directory (runtime-specific subdirectory on shared volume)
        self._models_dir = os.environ.get(
            "WHISPER_MODELS_DIR", "/models/faster-whisper"
        )

        # Auto-detect device and compute type
        self._device, self._compute_type = self._detect_device()
        self.logger.info(
            "engine_init",
            engine_id=self._engine_id,
            default_model=self._default_model_id,
            device=self._device,
            compute_type=self._compute_type,
            models_dir=self._models_dir,
        )

    def _detect_device(self) -> tuple[str, str]:
        """Detect the best available device and compute type.

        Phase 1: All Whisper models including large-v3-turbo can now run on CPU
        with int8 compute type. The previous GPU-only restriction was a performance
        guard, not a technical limitation. CTranslate2 supports CPU inference for
        all model sizes.

        Returns:
            Tuple of (device, compute_type)
        """
        # Check for explicit device override
        requested_device = os.environ.get("DALSTON_DEVICE", "").lower()

        if requested_device == "cpu":
            self.logger.info(
                "using_cpu_device",
                message="Running on CPU with int8 compute - inference will be slower than GPU",
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

        # Fallback to CPU (Phase 1: all models including large-v3-turbo support CPU)
        self.logger.info(
            "cuda_not_available",
            message="CUDA not available, falling back to CPU with int8 compute",
        )
        return "cpu", "int8"

    def _ensure_model_loaded(self, runtime_model_id: str) -> None:
        """Ensure the requested model is loaded, swapping if necessary.

        This method implements the model lifecycle management for Phase 1.
        If the requested model is already loaded, it returns immediately.
        Otherwise, it unloads the current model and loads the requested one.

        Args:
            runtime_model_id: Whisper model identifier (e.g., "large-v3-turbo")
                              This is passed directly to WhisperModel()

        Raises:
            ValueError: If the runtime_model_id is not in SUPPORTED_MODELS
        """
        # Fast path: requested model is already loaded
        if runtime_model_id == self._loaded_model_id:
            return

        # Validate the requested model
        if runtime_model_id not in self.SUPPORTED_MODELS:
            raise ValueError(
                f"Unknown model: {runtime_model_id}. "
                f"Supported models: {sorted(self.SUPPORTED_MODELS)}"
            )

        # Unload current model if one is loaded
        if self._model is not None:
            self.logger.info(
                "unloading_model",
                current=self._loaded_model_id,
                requested=runtime_model_id,
            )
            self._set_runtime_state(status="unloading")

            # Delete model reference
            del self._model
            self._model = None
            self._loaded_model_id = None

            # Memory cleanup
            gc.collect()

            self.logger.info("model_unloaded")

        # Load the requested model
        self._set_runtime_state(status="loading")
        self.logger.info(
            "loading_whisper_model",
            runtime_model_id=runtime_model_id,
            device=self._device,
            compute_type=self._compute_type,
            download_root=self._models_dir,
        )

        self._model = WhisperModel(
            runtime_model_id,
            device=self._device,
            compute_type=self._compute_type,
            download_root=self._models_dir,
        )
        self._loaded_model_id = runtime_model_id

        # Update runtime state for heartbeat reporting
        self._set_runtime_state(loaded_model=runtime_model_id, status="idle")
        self.logger.info(
            "model_loaded_successfully",
            runtime_model_id=runtime_model_id,
            device=self._device,
        )

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

        # Get model to use from task config, falling back to default
        # Phase 1: runtime_model_id is passed directly to WhisperModel()
        runtime_model_id = config.get("runtime_model_id", self._default_model_id)

        # Load model (with swapping if needed)
        self._ensure_model_loaded(runtime_model_id)

        self.logger.info("transcribing", audio_path=str(audio_path))
        self.logger.info(
            "transcribe_config",
            runtime_model_id=runtime_model_id,
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

        output = TranscribeOutput(
            text=full_text,
            segments=segments,
            language=info.language,
            language_confidence=round(info.language_probability, 3),
            duration=info.duration,
            timestamp_granularity_requested=TimestampGranularity.WORD,
            timestamp_granularity_actual=timestamp_granularity_actual,
            channel=channel,
            engine_id=self._engine_id,
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
            "engine_id": self._engine_id,
            "model_loaded": self._model is not None,
            "loaded_model_id": self._loaded_model_id,
            "device": self._device,
            "compute_type": self._compute_type,
            "cuda_available": cuda_available,
            "cuda_device_count": cuda_device_count,
        }

    # Capabilities are loaded from engine.yaml by the base class.
    # word_timestamps is set to false because attention-based timestamps
    # are not as accurate as forced alignment (whisperx-align).
    # Phase 1: The base class loads capabilities from engine.yaml which should
    # be the runtime-level YAML (not variant-specific) with runtime="faster-whisper".


if __name__ == "__main__":
    engine = WhisperEngine()
    engine.run()
