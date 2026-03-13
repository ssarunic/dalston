"""HuggingFace Transformers ASR engine.

Generic engine for any HuggingFace model with pipeline_tag=automatic-speech-recognition.
Supports Whisper, Wav2Vec2, HuBERT, MMS, and community fine-tunes.

Uses the HuggingFace ``transformers.pipeline("automatic-speech-recognition")``
abstraction to normalize loading and inference across model architectures.

Features:
    - Runtime model swapping via config["loaded_model_id"]
    - TTL-based model eviction for idle models
    - LRU eviction when at max_loaded capacity
    - Multi-model support on single GPU
    - Output normalization across architectures to Dalston format

Environment variables:
    DALSTON_ENGINE_ID: Runtime engine ID for registration (default: "hf-asr")
    DALSTON_DEFAULT_MODEL_ID: Default HF model ID (default: "openai/whisper-large-v3")
    DALSTON_DEVICE: Device for inference (cuda, cpu). Defaults to cuda if available.
    DALSTON_MODEL_TTL_SECONDS: Evict models idle longer than this (default: 3600)
    DALSTON_MAX_LOADED_MODELS: Maximum models to keep loaded (default: 2)
    DALSTON_MODEL_PRELOAD: Model to preload on startup (optional)
    DALSTON_S3_BUCKET: S3 bucket for models (enables S3 storage)
"""

import os
from typing import Any

import torch

from dalston.common.pipeline_types import (
    AlignmentMethod,
    Transcript,
    TranscriptSegment,
    TranscriptWord,
)
from dalston.engine_sdk import (
    BatchTaskContext,
    EngineInput,
)
from dalston.engine_sdk.base_transcribe import BaseBatchTranscribeEngine
from dalston.engine_sdk.managers import HFTransformersModelManager


class HfAsrBatchEngine(BaseBatchTranscribeEngine):
    """Generic HuggingFace ASR pipeline engine.

    This engine uses HFTransformersModelManager to handle model lifecycle:
    - Models are loaded on first request for that model
    - Multiple models can be loaded simultaneously (up to max_loaded)
    - Idle models are evicted after TTL expires
    - When at capacity, least-recently-used models are evicted first

    Automatically detects GPU availability and selects appropriate dtype:
    - GPU (CUDA): float16 for performance
    - CPU: float32 for compatibility
    """

    DEFAULT_MODEL_ID = "openai/whisper-large-v3"

    def __init__(self, manager: HFTransformersModelManager | None = None) -> None:
        """Initialize the engine.

        Args:
            manager: Optional shared HFTransformersModelManager. If provided,
                     the engine uses it instead of creating its own. This is
                     how the unified runner shares a single model manager
                     between batch and RT adapters.
        """
        super().__init__()

        self._default_model_id = os.environ.get(
            "DALSTON_DEFAULT_MODEL_ID", self.DEFAULT_MODEL_ID
        )
        self._engine_id = os.environ.get("DALSTON_ENGINE_ID", "hf-asr")

        if manager is not None:
            self._manager = manager
            self._device = manager.device
            self._torch_dtype = manager.torch_dtype
        else:
            # Auto-detect device and dtype
            self._device, self._torch_dtype = self._detect_device()

            # Configure S3 storage if bucket is set
            model_storage = None
            s3_bucket = os.environ.get("DALSTON_S3_BUCKET")
            if s3_bucket:
                from dalston.engine_sdk.model_storage import S3ModelStorage

                model_storage = S3ModelStorage.from_env()
                self.logger.info("s3_model_storage_enabled", bucket=s3_bucket)

            # Initialize model manager with TTL eviction
            self._manager = HFTransformersModelManager(
                device=self._device,
                torch_dtype=self._torch_dtype,
                model_storage=model_storage,
                ttl_seconds=int(os.environ.get("DALSTON_MODEL_TTL_SECONDS", "3600")),
                max_loaded=int(os.environ.get("DALSTON_MAX_LOADED_MODELS", "2")),
                preload=os.environ.get("DALSTON_MODEL_PRELOAD"),
            )

        self.logger.info(
            "engine_init",
            engine_id=self._engine_id,
            default_model=self._default_model_id,
            device=self._device,
            torch_dtype=str(self._torch_dtype),
            ttl_seconds=self._manager.ttl_seconds,
            max_loaded=self._manager.max_loaded,
            shared_manager=manager is not None,
        )

    def _detect_device(self) -> tuple[str, torch.dtype]:
        """Detect the best available device and dtype.

        Returns:
            Tuple of (device, torch_dtype)
        """
        requested_device = os.environ.get("DALSTON_DEVICE", "").lower()

        if requested_device == "cpu":
            self.logger.info(
                "using_cpu_device",
                message="Running on CPU with float32 - inference will be slower than GPU",
            )
            return "cpu", torch.float32

        if torch.cuda.is_available():
            return "cuda", torch.float16

        if requested_device == "cuda":
            raise RuntimeError("DALSTON_DEVICE=cuda but CUDA is not available.")

        if requested_device not in ("", "auto"):
            raise ValueError(
                f"Unknown DALSTON_DEVICE value: {requested_device}. Use cuda or cpu."
            )

        self.logger.info(
            "cuda_not_available",
            message="CUDA not available, falling back to CPU with float32",
        )
        return "cpu", torch.float32

    def transcribe_audio(
        self, engine_input: EngineInput, ctx: BatchTaskContext
    ) -> Transcript:
        """Transcribe audio using a HuggingFace ASR pipeline.

        Args:
            engine_input: Task input with audio file path and config
            ctx: Batch task context for tracing/logging

        Returns:
            Transcript with text, segments, and language
        """
        params = engine_input.get_transcribe_params()

        # Get model to use from task config
        loaded_model_id = params.loaded_model_id or self._default_model_id

        # Handle language: None or "auto" means auto-detect
        language = params.language
        if language == "auto" or language == "":
            language = None

        channel = params.channel

        # Acquire pipeline from manager (loads if needed, updates LRU)
        pipe = self._manager.acquire(loaded_model_id)
        try:
            # Update engine_id state for heartbeat reporting
            self._set_runtime_state(loaded_model=loaded_model_id, status="processing")

            self.logger.info(
                "transcribing",
                audio_path=str(engine_input.audio_path),
                loaded_model_id=loaded_model_id,
                language=language,
            )

            # Build pipeline kwargs
            pipe_kwargs: dict[str, Any] = {}

            # Request word-level timestamps when supported
            pipe_kwargs["return_timestamps"] = "word"

            # Pass language and vocabulary for models that support it (e.g. Whisper)
            generate_kwargs: dict[str, Any] = {}
            if language:
                generate_kwargs["language"] = language

            # Vocabulary → initial_prompt for Whisper models (prompt conditioning)
            vocabulary = params.vocabulary
            if vocabulary:
                generate_kwargs["prompt"] = ", ".join(vocabulary)
                self.logger.debug(
                    "vocabulary_as_prompt",
                    terms_count=len(vocabulary),
                    loaded_model_id=loaded_model_id,
                )

            if generate_kwargs:
                pipe_kwargs["generate_kwargs"] = generate_kwargs

            # Run ASR pipeline
            result = pipe(str(engine_input.audio_path), **pipe_kwargs)

            # Normalize output to Transcript format
            transcript = self._normalize_output(
                result, loaded_model_id, language, channel
            )

            self.logger.info(
                "transcription_complete",
                segment_count=len(transcript.segments),
                char_count=len(transcript.text),
            )

            return transcript

        finally:
            # Always release the model reference
            self._manager.release(loaded_model_id)
            self._set_runtime_state(status="idle")

    def _normalize_output(
        self,
        result: dict[str, Any],
        model_id: str,
        language: str | None,
        channel: int | None,
    ) -> Transcript:
        """Normalize HuggingFace pipeline output to Transcript.

        HF pipeline returns different formats based on model architecture:
        - Whisper: {"text": "...", "chunks": [{"text": "...", "timestamp": (start, end)}]}
        - Wav2Vec2: {"text": "..."}  (no timestamps by default)
        - MMS: {"text": "..."}  (no timestamps)

        Args:
            result: Raw pipeline output dict
            model_id: HuggingFace model ID for logging
            language: Requested language or None
            channel: Audio channel index or None

        Returns:
            Normalized Transcript
        """
        text = result.get("text", "").strip()
        chunks = result.get("chunks", [])

        segments: list[TranscriptSegment] = []
        has_word_timestamps = False

        if chunks:
            # Process chunks with timestamps
            words: list[TranscriptWord] = []

            for chunk in chunks:
                chunk_text = chunk.get("text", "").strip()
                if not chunk_text:
                    continue

                timestamp = chunk.get("timestamp", (None, None))
                start = timestamp[0] if timestamp and timestamp[0] is not None else 0.0
                end = timestamp[1] if timestamp and timestamp[1] is not None else 0.0

                words.append(
                    self.build_word(
                        text=chunk_text,
                        start=round(start, 3),
                        end=round(end, 3),
                        confidence=None,
                        alignment_method=AlignmentMethod.ATTENTION,
                    )
                )

            has_word_timestamps = bool(words)

            if words:
                # Group words into segments (one segment for now; downstream
                # stages like alignment or merge can re-segment)
                segments.append(
                    self.build_segment(
                        start=round(words[0].start, 3),
                        end=round(words[-1].end, 3),
                        text=text,
                        words=words,
                    )
                )
            else:
                segments.append(
                    self.build_segment(
                        start=0.0,
                        end=0.0,
                        text=text,
                    )
                )
        else:
            # No timestamps - create single segment
            segments.append(
                self.build_segment(
                    start=0.0,
                    end=0.0,
                    text=text,
                )
            )

        return self.build_transcript(
            text=text,
            segments=segments,
            language=language or "auto",
            engine_id=self._engine_id,
            alignment_method=(
                AlignmentMethod.ATTENTION
                if has_word_timestamps
                else AlignmentMethod.UNKNOWN
            ),
            channel=channel,
        )

    def health_check(self) -> dict[str, Any]:
        """Return health status including GPU availability and model stats."""
        cuda_available = torch.cuda.is_available()
        cuda_device_count = torch.cuda.device_count() if cuda_available else 0
        manager_stats = self._manager.get_stats()

        return {
            "status": "healthy",
            "engine_id": self._engine_id,
            "device": self._device,
            "torch_dtype": str(self._torch_dtype),
            "cuda_available": cuda_available,
            "cuda_device_count": cuda_device_count,
            "model_manager": manager_stats,
        }

    def get_local_cache_stats(self) -> dict[str, Any] | None:
        """Get local model cache statistics for heartbeat reporting.

        Returns cache stats from S3ModelStorage if configured.
        """
        return self._manager.get_local_cache_stats()

    def shutdown(self) -> None:
        """Shutdown engine and cleanup resources."""
        self.logger.info("engine_shutdown")
        self._manager.shutdown()
        super().shutdown()


if __name__ == "__main__":
    engine = HfAsrBatchEngine()
    engine.run()
