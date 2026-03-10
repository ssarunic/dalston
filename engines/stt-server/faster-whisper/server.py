"""Faster-whisper gRPC inference server (sidecar pattern).

Wraps TranscribeCore in a standalone gRPC server so batch and RT engines
can be thin CPU-only clients connecting over the network.

Usage:
    python server.py

Environment variables:
    DALSTON_SERVER_PORT: gRPC port (default: 50052)
    DALSTON_MAX_CONCURRENT: Max concurrent requests (default: 4)
    DALSTON_DEVICE: Device (cuda, cpu). Defaults to auto-detect.
    DALSTON_DEFAULT_MODEL_ID: Default model (default: large-v3-turbo)
    DALSTON_MODEL_TTL_SECONDS: Idle model TTL (default: 3600)
    DALSTON_MAX_LOADED_MODELS: Max models in memory (default: 2)
    DALSTON_MODEL_PRELOAD: Model to preload on startup (optional)
"""

from __future__ import annotations

import os
from typing import Any

import numpy as np
import structlog

from dalston.engine_sdk.cores.faster_whisper_core import (
    TranscribeConfig,
    TranscribeCore,
)
from dalston.engine_sdk.inference_server import InferenceServer
from dalston.proto import inference_pb2

logger = structlog.get_logger()


class FasterWhisperServer(InferenceServer):
    """gRPC inference server wrapping TranscribeCore."""

    def __init__(self) -> None:
        core = TranscribeCore.from_env()
        port = int(os.environ.get("DALSTON_SERVER_PORT", "50052"))
        max_concurrent = int(os.environ.get("DALSTON_MAX_CONCURRENT", "4"))
        super().__init__(core=core, port=port, max_concurrent=max_concurrent)

    def get_runtime(self) -> str:
        return "faster-whisper"

    def _do_transcribe(
        self, audio: str | np.ndarray, model_id: str, config: dict[str, Any]
    ) -> inference_pb2.TranscribeResponse:
        """Transcribe using TranscribeCore and convert to proto response."""
        if not model_id:
            model_id = os.environ.get("DALSTON_DEFAULT_MODEL_ID", "large-v3-turbo")

        tc = TranscribeConfig(
            language=config.get("language"),
            beam_size=config.get("beam_size", 5),
            vad_filter=config.get("vad_filter", True),
            word_timestamps=config.get("word_timestamps", True),
            temperature=config.get("temperature", 0.0),
            task=config.get("task", "transcribe"),
            initial_prompt=config.get("initial_prompt"),
            hotwords=config.get("hotwords"),
        )

        result = self._core.transcribe(audio=audio, model_id=model_id, config=tc)

        # Convert to proto
        segments = []
        for seg in result.segments:
            words = [
                inference_pb2.Word(
                    word=w.word,
                    start=w.start,
                    end=w.end,
                    probability=w.probability,
                )
                for w in seg.words
            ]
            segments.append(
                inference_pb2.Segment(
                    start=seg.start,
                    end=seg.end,
                    text=seg.text,
                    words=words,
                    avg_logprob=seg.avg_logprob,
                    compression_ratio=seg.compression_ratio,
                    no_speech_prob=seg.no_speech_prob,
                )
            )

        return inference_pb2.TranscribeResponse(
            segments=segments,
            language=result.language,
            language_probability=result.language_probability,
            duration=result.duration,
        )

    def _get_loaded_models(self) -> list[str]:
        stats = self._core.get_stats()
        return stats.get("loaded_models", [])


if __name__ == "__main__":
    FasterWhisperServer().run()
