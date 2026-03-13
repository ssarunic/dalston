"""Riva NIM real-time transcription adapter.

Uses offline_recognize() via the SessionHandler's periodic re-transcription
pattern.  Delegates gRPC communication to the shared RivaClient.

When run standalone, creates its own RivaClient.  When used within the
unified runner, accepts an injected client to share a single gRPC channel
with the batch adapter.
"""

from __future__ import annotations

import os
from typing import Any

import numpy as np
import structlog
from riva_client import SUPPORTED_LANGUAGES, RivaClient

from dalston.common.pipeline_types import (
    AlignmentMethod,
    TranscribeInput,
    Transcript,
    TranscriptWord,
)
from dalston.realtime_sdk.base_transcribe import BaseRealtimeTranscribeEngine

logger = structlog.get_logger()


class RivaRealtimeEngine(BaseRealtimeTranscribeEngine):
    """Real-time transcription engine using Riva NIM gRPC.

    Uses offline_recognize() via the SessionHandler's periodic
    re-transcription pattern.  supports_streaming() returns True to
    enable partial results during speech.
    """

    def __init__(self, core: RivaClient | None = None) -> None:
        super().__init__()
        self._core: RivaClient | None = core
        self._engine_id = os.environ.get("DALSTON_ENGINE_ID", "riva")

    def load_models(self) -> None:
        """Initialize gRPC connection to NIM sidecar.

        If a RivaClient was injected via __init__, this method uses it
        instead of creating a new one.
        """
        if self._core is None:
            self._core = RivaClient.from_env()

        logger.info(
            "riva_nim_ready",
            uri=self._core.uri,
            shared_core=True,
        )

    def transcribe_v1(self, audio: np.ndarray, params: TranscribeInput) -> Transcript:
        """Transcribe an audio segment via Riva NIM.

        Called by SessionHandler when VAD detects an utterance endpoint
        or periodically during speech (when supports_streaming=True).
        """
        if self._core is None:
            raise RuntimeError("RivaClient not initialized -- call load_models() first")

        # Convert float32 [-1.0, 1.0] to int16 bytes for Riva
        audio_int16 = (audio * 32767).astype(np.int16)
        audio_bytes = audio_int16.tobytes()

        lang_code = (
            params.language if params.language and params.language != "auto" else "en"
        )

        response = self._core.offline_recognize(audio_bytes, lang_code)

        segments = []
        text_parts: list[str] = []
        max_confidence = 0.0

        for result in response.results:
            if not result.alternatives:
                continue
            alt = result.alternatives[0]
            transcript = alt.transcript.strip()
            if transcript:
                text_parts.append(transcript)
            max_confidence = max(max_confidence, alt.confidence)

            words: list[TranscriptWord] = []
            for w in alt.words:
                words.append(
                    self.build_word(
                        text=w.word,
                        start=w.start_time,
                        end=w.end_time,
                        confidence=w.confidence,
                        alignment_method=AlignmentMethod.UNKNOWN,
                    )
                )

            if transcript:
                seg_start = words[0].start if words else 0.0
                seg_end = words[-1].end if words else 0.0
                segments.append(
                    self.build_segment(
                        start=seg_start,
                        end=seg_end,
                        text=transcript,
                        words=words if words else None,
                        confidence=alt.confidence,
                    )
                )

        return self.build_transcript(
            text=" ".join(text_parts),
            segments=segments,
            language=lang_code,
            engine_id=self._engine_id,
            language_confidence=max_confidence,
        )

    def supports_streaming(self) -> bool:
        """Enable partial results via SessionHandler's periodic re-transcription."""
        return True

    def get_models(self) -> list[str]:
        """NIM manages models -- no local model selection."""
        return []

    def get_languages(self) -> list[str]:
        return SUPPORTED_LANGUAGES

    def get_engine_id(self) -> str:
        return self._engine_id

    def get_supports_vocabulary(self) -> bool:
        return False

    def get_gpu_memory_usage(self) -> str:
        """No local GPU usage -- NIM handles GPU."""
        return "0GB"

    def health_check(self) -> dict[str, Any]:
        base_health = super().health_check()
        if self._core is not None:
            nim_health = self._core.health_check()
            return {**base_health, **nim_health}
        return {**base_health, "nim": "not_initialized"}

    def shutdown(self) -> None:
        logger.info("riva_rt_shutdown")


if __name__ == "__main__":
    import asyncio

    engine = RivaRealtimeEngine()
    asyncio.run(engine.run())
