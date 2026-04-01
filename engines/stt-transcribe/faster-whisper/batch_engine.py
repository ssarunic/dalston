"""Faster-Whisper transcription engine with TTL-based model management.

Uses the faster-whisper library (CTranslate2-based) for efficient
speech-to-text transcription with GPU acceleration.

Delegates inference to FasterWhisperInference (shared with the realtime engine).

Features:
    - Runtime model swapping via config["loaded_model_id"]
    - TTL-based model eviction for idle models
    - LRU eviction when at max_loaded capacity
    - Multi-model support on single GPU

Environment variables:
    DALSTON_ENGINE_ID: Runtime engine ID for registration (default: "faster-whisper")
    DALSTON_DEFAULT_MODEL: Default model ID (default: "large-v3-turbo")
    DALSTON_DEVICE: Device to use for inference (cuda, cpu). Defaults to cuda if available.
    DALSTON_MODEL_TTL_SECONDS: Evict models idle longer than this (default: 3600)
    DALSTON_MAX_LOADED_MODELS: Maximum models to keep loaded (default: 2)
    DALSTON_MODEL_PRELOAD: Model to preload on startup (optional)
    WHISPER_MODELS_DIR: Directory for model cache (default: /models/ctranslate2/faster-whisper)
"""

import os
from typing import Any

from dalston.common.pipeline_types import (
    AlignmentMethod,
    Transcript,
    TranscriptSegment,
    TranscriptWord,
)
from dalston.engine_sdk import BatchTaskContext, TaskRequest
from dalston.engine_sdk.base_transcribe import BaseBatchTranscribeEngine
from dalston.engine_sdk.inference.faster_whisper_inference import (
    FasterWhisperConfig,
    FasterWhisperInference,
)


class FasterWhisperBatchEngine(BaseBatchTranscribeEngine):
    """Faster-Whisper transcription engine with TTL-based model management.

    This engine delegates inference to FasterWhisperInference, which is shared
    with the realtime faster-whisper engine. The batch adapter handles:
    - Task config parsing
    - Output formatting to Transcript
    - Heartbeat state reporting

    When run standalone, creates its own FasterWhisperInference. When used within
    a unified runner, accepts an injected core to share a single loaded
    model with the realtime adapter.

    Automatically detects GPU availability and selects appropriate compute type:
    - GPU (CUDA): float16 for maximum performance
    - CPU: int8 for efficient inference (all models including large-v3-turbo)
    """

    ENGINE_ID = "faster-whisper"
    DEFAULT_BEAM_SIZE = 5
    DEFAULT_VAD_FILTER = True
    DEFAULT_MODEL = "large-v3-turbo"

    def __init__(self, core: FasterWhisperInference | None = None) -> None:
        super().__init__()

        # Get configuration from environment
        self._default_model_id = os.environ.get(
            "DALSTON_DEFAULT_MODEL", self.DEFAULT_MODEL
        )

        # Use injected core (unified runner) or create own (standalone)
        self._core = core if core is not None else FasterWhisperInference.from_env()

        self.logger.info(
            "engine_init",
            engine_id=self.engine_id,
            default_model=self._default_model_id,
            device=self._core.device,
            compute_type=self._core.compute_type,
            ttl_seconds=self._core.manager.ttl_seconds,
            max_loaded=self._core.manager.max_loaded,
            shared_core=core is not None,
        )

    def transcribe_audio(
        self, task_request: TaskRequest, ctx: BatchTaskContext
    ) -> Transcript:
        """Transcribe audio using Faster-Whisper via shared FasterWhisperInference.

        Args:
            task_request: Task input with audio file path and config
            ctx: Batch task context for tracing/logging

        Returns:
            Transcript with text, segments, and language
        """
        audio_path = task_request.audio_path
        params = task_request.get_transcribe_params()

        # Parse config into FasterWhisperConfig
        channel = params.channel
        vocabulary = params.vocabulary
        prompt = params.prompt
        raw_temperature = params.temperature

        if isinstance(raw_temperature, list):
            parsed_temperatures = [float(value) for value in raw_temperature]
            decode_temperature: float | list[float] = (
                parsed_temperatures if parsed_temperatures else 0.0
            )
            segment_temperature = parsed_temperatures[0] if parsed_temperatures else 0.0
        else:
            segment_temperature = (
                float(raw_temperature) if raw_temperature is not None else 0.0
            )
            decode_temperature = segment_temperature

        # Get model to use from task config
        loaded_model_id = params.loaded_model_id or self._default_model_id

        # Select vad_batch_size: explicit config > adaptive VRAM budget > 1
        if params.vad_batch_size is not None:
            adaptive_vad_batch_size = params.vad_batch_size
        else:
            adaptive_vad_batch_size = self._resolve_adaptive_batch_size(fallback=1)

        # Build transcribe config
        hotwords = " ".join(vocabulary) if vocabulary else None
        transcribe_config = FasterWhisperConfig(
            language=params.language,
            beam_size=(
                params.beam_size
                if params.beam_size is not None
                else self.DEFAULT_BEAM_SIZE
            ),
            vad_batch_size=adaptive_vad_batch_size,
            vad_filter=params.vad_filter,
            word_timestamps=(
                True if params.word_timestamps is None else params.word_timestamps
            ),
            temperature=decode_temperature,
            task=params.task,
            initial_prompt=prompt,
            hotwords=hotwords,
        )

        # Update engine_id state for heartbeat reporting
        self._set_runtime_state(loaded_model=loaded_model_id, status="processing")

        self.logger.info(
            "transcribing",
            audio_path=str(audio_path),
            vad_batch_size=adaptive_vad_batch_size,
        )
        self.logger.info(
            "transcribe_config",
            loaded_model_id=loaded_model_id,
            language=transcribe_config.language,
            beam_size=transcribe_config.beam_size,
            vad_batch_size=transcribe_config.vad_batch_size,
            vad_filter=transcribe_config.vad_filter,
            vocabulary_terms=len(vocabulary) if vocabulary else 0,
            has_prompt=bool(prompt),
            task=transcribe_config.task,
            temperature=decode_temperature,
        )

        try:
            # Delegate to shared core
            result = self._core.transcribe(
                audio=audio_path,
                model_id=loaded_model_id,
                config=transcribe_config,
            )

            # Format core result into Transcript
            segments: list[TranscriptSegment] = []
            full_text_parts: list[str] = []

            for seg in result.segments:
                words: list[TranscriptWord] | None = None
                if seg.words:
                    words = [
                        self.build_word(
                            text=w.word,
                            start=w.start,
                            end=w.end,
                            confidence=w.probability,
                            alignment_method=AlignmentMethod.ATTENTION,
                        )
                        for w in seg.words
                    ]

                segments.append(
                    self.build_segment(
                        start=seg.start,
                        end=seg.end,
                        text=seg.text,
                        words=words,
                        # Whisper-specific fields go into metadata
                        tokens=seg.tokens,
                        temperature=segment_temperature,
                        avg_logprob=seg.avg_logprob,
                        compression_ratio=seg.compression_ratio,
                        no_speech_prob=seg.no_speech_prob,
                    )
                )
                full_text_parts.append(seg.text)

            full_text = " ".join(full_text_parts)

            self.logger.info(
                "transcription_complete",
                segment_count=len(segments),
                char_count=len(full_text),
            )
            self.logger.info(
                "detected_language",
                language=result.language,
                confidence=round(result.language_probability, 2),
            )

            return self.build_transcript(
                text=full_text,
                segments=segments,
                language=result.language,
                engine_id=self.engine_id,
                language_confidence=round(result.language_probability, 3),
                duration=result.duration,
                alignment_method=AlignmentMethod.ATTENTION,
                channel=channel,
            )

        finally:
            self._set_runtime_state(status="idle")

    def health_check(self) -> dict[str, Any]:
        return {
            **super().health_check(),
            "device": self._core.device,
            "compute_type": self._core.compute_type,
            "model_manager": self._core.get_stats(),
        }

    def get_local_cache_stats(self) -> dict[str, Any] | None:
        """Get local model cache statistics for heartbeat reporting."""
        return self._core.get_local_cache_stats()

    def shutdown(self) -> None:
        """Shutdown engine and cleanup resources."""
        self.logger.info("engine_shutdown")
        self._core.shutdown()
        super().shutdown()


if __name__ == "__main__":
    engine = FasterWhisperBatchEngine()
    engine.run()
