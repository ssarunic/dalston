"""Parakeet gRPC inference server (sidecar pattern).

Wraps ParakeetCore in a standalone gRPC server so batch and RT engines
can be thin CPU-only clients connecting over the network.

Usage:
    python server.py

Environment variables:
    DALSTON_SERVER_PORT: gRPC port (default: 50053)
    DALSTON_MAX_CONCURRENT: Max concurrent requests (default: 4)
    DALSTON_DEVICE: Device (cuda, cpu). Defaults to auto-detect.
    DALSTON_DEFAULT_MODEL_ID: Default model (default: nvidia/parakeet-tdt-1.1b)
    DALSTON_MODEL_TTL_SECONDS: Idle model TTL (default: 3600)
    DALSTON_MAX_LOADED_MODELS: Max models in memory (default: 2)
    DALSTON_MODEL_PRELOAD: Model to preload on startup (optional)
"""

from __future__ import annotations

import os
from typing import Any

import numpy as np
import structlog

from dalston.engine_sdk.cores.parakeet_core import ParakeetCore
from dalston.engine_sdk.inference_server import InferenceServer
from dalston.proto import inference_pb2

logger = structlog.get_logger()


class ParakeetServer(InferenceServer):
    """gRPC inference server wrapping ParakeetCore."""

    def __init__(self) -> None:
        core = ParakeetCore.from_env()
        port = int(os.environ.get("DALSTON_SERVER_PORT", "50053"))
        max_concurrent = int(os.environ.get("DALSTON_MAX_CONCURRENT", "4"))
        super().__init__(core=core, port=port, max_concurrent=max_concurrent)

    def get_runtime(self) -> str:
        return "parakeet"

    def _do_transcribe(
        self, audio: str | np.ndarray, model_id: str, config: dict[str, Any]
    ) -> inference_pb2.TranscribeResponse:
        """Transcribe using ParakeetCore and convert to proto response."""
        if not model_id:
            model_id = os.environ.get(
                "DALSTON_DEFAULT_MODEL_ID", "nvidia/parakeet-tdt-1.1b"
            )

        # Strip nvidia/ prefix for NeMoModelManager
        if "/" in model_id:
            model_id = model_id.split("/", 1)[1]

        result = self._core.transcribe(audio=audio, model_id=model_id)

        # Convert to proto
        segments = []
        for seg in result.segments:
            words = [
                inference_pb2.Word(
                    word=w.word,
                    start=w.start,
                    end=w.end,
                    probability=w.confidence or 0.95,
                )
                for w in seg.words
            ]
            segments.append(
                inference_pb2.Segment(
                    start=seg.start,
                    end=seg.end,
                    text=seg.text,
                    words=words,
                )
            )

        return inference_pb2.TranscribeResponse(
            segments=segments,
            language="en",
            language_probability=1.0,
            duration=0.0,
        )

    def _get_loaded_models(self) -> list[str]:
        stats = self._core.get_stats()
        return stats.get("loaded_models", [])


if __name__ == "__main__":
    ParakeetServer().run()
