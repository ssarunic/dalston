"""Faster-Whisper transcription engine with TTL-based model management.

Uses the faster-whisper library (CTranslate2-based) for efficient
speech-to-text transcription with GPU acceleration.

Delegates inference to TranscribeCore (shared with the realtime engine).

Features:
    - Runtime model swapping via config["runtime_model_id"]
    - TTL-based model eviction for idle models
    - LRU eviction when at max_loaded capacity
    - Multi-model support on single GPU

Environment variables:
    DALSTON_RUNTIME: Runtime engine ID for registration (default: "faster-whisper")
    DALSTON_DEFAULT_MODEL_ID: Default model ID (default: "large-v3-turbo")
    DALSTON_DEVICE: Device to use for inference (cuda, cpu). Defaults to cuda if available.
    DALSTON_MODEL_TTL_SECONDS: Evict models idle longer than this (default: 3600)
    DALSTON_MAX_LOADED_MODELS: Maximum models to keep loaded (default: 2)
    DALSTON_MODEL_PRELOAD: Model to preload on startup (optional)
    WHISPER_MODELS_DIR: Directory for model cache (default: /models/ctranslate2/faster-whisper)
"""

import os
from typing import Any

from dalston.engine_sdk import (
    AlignmentMethod,
    BatchTaskContext,
    Engine,
    EngineInput,
    EngineOutput,
    Segment,
    TimestampGranularity,
    TranscribeOutput,
    Word,
)
from dalston.engine_sdk.cores.faster_whisper_core import (
    TranscribeConfig,
    TranscribeCore,
)


class WhisperEngine(Engine):
    """Faster-Whisper transcription engine with TTL-based model management.

    This engine delegates inference to TranscribeCore, which is shared
    with the realtime faster-whisper engine. The batch adapter handles:
    - Task config parsing
    - Output formatting to TranscribeOutput
    - Heartbeat state reporting

    When run standalone, creates its own TranscribeCore. When used within
    a unified runner, accepts an injected core to share a single loaded
    model with the realtime adapter.

    Automatically detects GPU availability and selects appropriate compute type:
    - GPU (CUDA): float16 for maximum performance
    - CPU: int8 for efficient inference (all models including large-v3-turbo)
    """

    DEFAULT_BEAM_SIZE = 5
    DEFAULT_VAD_FILTER = True
    DEFAULT_MODEL_ID = "large-v3-turbo"

    def __init__(self, core: TranscribeCore | None = None) -> None:
        super().__init__()

        # Get configuration from environment
        self._default_model_id = os.environ.get(
            "DALSTON_DEFAULT_MODEL_ID", self.DEFAULT_MODEL_ID
        )
        self._runtime = os.environ.get("DALSTON_RUNTIME", "faster-whisper")

        # Three modes:
        # 1. Injected core (unified runner) — use it directly
        # 2. DALSTON_INFERENCE_URI set — sidecar mode, gRPC to inference server
        # 3. Neither — standalone mode, create own TranscribeCore
        if core is not None:
            self._core = core
        else:
            inference_uri = os.environ.get("DALSTON_INFERENCE_URI")
            if inference_uri:
                from dalston.engine_sdk.cores.remote_core import RemoteTranscribeCore

                self._core = RemoteTranscribeCore(inference_uri)
            else:
                self._core = TranscribeCore.from_env()

        self.logger.info(
            "engine_init",
            runtime=self._runtime,
            default_model=self._default_model_id,
            device=self._core.device,
            compute_type=getattr(self._core, "compute_type", "remote"),
            shared_core=core is not None,
            sidecar_mode=os.environ.get("DALSTON_INFERENCE_URI") is not None,
        )

    def process(self, engine_input: EngineInput, ctx: BatchTaskContext) -> EngineOutput:
        """Transcribe audio using Faster-Whisper via shared TranscribeCore.

        Args:
            engine_input: Task input with audio file path and config
            ctx: Batch task context for tracing/logging

        Returns:
            EngineOutput with TranscribeOutput containing text, segments, and language
        """
        audio_path = engine_input.audio_path
        config = engine_input.config

        # Parse config into TranscribeConfig
        channel = config.get("channel")
        vocabulary = config.get("vocabulary")
        prompt = config.get("prompt")
        raw_temperature = config.get("temperature", 0.0)

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
        runtime_model_id = config.get("runtime_model_id", self._default_model_id)

        # Build transcribe config
        hotwords = " ".join(vocabulary) if vocabulary else None
        transcribe_config = TranscribeConfig(
            language=config.get("language"),
            beam_size=config.get("beam_size", self.DEFAULT_BEAM_SIZE),
            vad_filter=config.get("vad_filter", self.DEFAULT_VAD_FILTER),
            word_timestamps=True,
            temperature=decode_temperature,
            task=config.get("task", "transcribe"),
            initial_prompt=prompt,
            hotwords=hotwords,
        )

        # Update runtime state for heartbeat reporting
        self._set_runtime_state(loaded_model=runtime_model_id, status="processing")

        self.logger.info("transcribing", audio_path=str(audio_path))
        self.logger.info(
            "transcribe_config",
            runtime_model_id=runtime_model_id,
            language=transcribe_config.language,
            beam_size=transcribe_config.beam_size,
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
                model_id=runtime_model_id,
                config=transcribe_config,
            )

            # Format core result into batch output contract
            segments: list[Segment] = []
            full_text_parts: list[str] = []

            for seg in result.segments:
                words: list[Word] | None = None
                if seg.words:
                    words = [
                        Word(
                            text=w.word,
                            start=w.start,
                            end=w.end,
                            confidence=w.probability,
                            alignment_method=AlignmentMethod.ATTENTION,
                        )
                        for w in seg.words
                    ]

                segments.append(
                    Segment(
                        start=seg.start,
                        end=seg.end,
                        text=seg.text,
                        words=words,
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

            has_word_timestamps = any(seg.words for seg in segments)
            timestamp_granularity_actual = (
                TimestampGranularity.WORD
                if has_word_timestamps
                else TimestampGranularity.SEGMENT
            )

            output = TranscribeOutput(
                text=full_text,
                segments=segments,
                language=result.language,
                language_confidence=round(result.language_probability, 3),
                duration=result.duration,
                timestamp_granularity_requested=TimestampGranularity.WORD,
                timestamp_granularity_actual=timestamp_granularity_actual,
                alignment_method=(
                    AlignmentMethod.ATTENTION if has_word_timestamps else None
                ),
                channel=channel,
                runtime=self._runtime,
                skipped=False,
                skip_reason=None,
                warnings=[],
            )

            return EngineOutput(data=output)

        finally:
            self._set_runtime_state(status="idle")

    def health_check(self) -> dict[str, Any]:
        """Return health status including GPU availability and model stats."""
        cuda_available = False
        cuda_device_count = 0

        try:
            import torch

            cuda_available = torch.cuda.is_available()
            cuda_device_count = torch.cuda.device_count() if cuda_available else 0
        except ImportError:
            pass

        manager_stats = self._core.get_stats()

        return {
            "status": "healthy",
            "runtime": self._runtime,
            "device": self._core.device,
            "compute_type": self._core.compute_type,
            "cuda_available": cuda_available,
            "cuda_device_count": cuda_device_count,
            "model_manager": manager_stats,
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
    engine = WhisperEngine()
    engine.run()
