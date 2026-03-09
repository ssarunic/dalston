"""Riva NIM batch transcription engine.

Thin gRPC client that delegates inference to a Riva NIM container's
offline_recognize API and maps results to Dalston's TranscribeOutput.
"""

from __future__ import annotations

import os
import time

import riva.client
import structlog

from dalston.common.pipeline_types import (
    AlignmentMethod,
    Segment,
    TimestampGranularity,
    TranscribeOutput,
    Word,
)
from dalston.engine_sdk.base import Engine
from dalston.engine_sdk.context import BatchTaskContext
from dalston.engine_sdk.types import EngineInput, EngineOutput

logger = structlog.get_logger()


class RivaEngine(Engine):
    """Thin transcription engine delegating to Riva NIM via gRPC."""

    def __init__(self) -> None:
        super().__init__()
        riva_url = os.environ["RIVA_GRPC_URL"]  # e.g. "riva-nim:50051"
        self._auth = riva.client.Auth(uri=riva_url)
        self._asr = riva.client.ASRService(self._auth)
        self.logger.info("riva_engine_initialized", riva_url=riva_url)

    def process(self, engine_input: EngineInput, ctx: BatchTaskContext) -> EngineOutput:
        audio_bytes = engine_input.audio_path.read_bytes()
        language = ctx.get_metadata("language", "en-US")

        # Normalize language code for Riva (expects BCP-47, e.g., "en-US")
        if len(language) == 2:
            language = f"{language}-US" if language == "en" else language

        config = riva.client.RecognitionConfig(
            language_code=language,
            max_alternatives=1,
            enable_automatic_punctuation=True,
            enable_word_time_offsets=True,
        )

        start_time = time.monotonic()
        response = self._asr.offline_recognize(audio_bytes, config)
        elapsed_ms = (time.monotonic() - start_time) * 1000

        self.logger.info(
            "riva_grpc_call_completed",
            task_id=ctx.task_id,
            duration_ms=round(elapsed_ms, 1),
            results_count=len(response.results),
        )

        return self._build_output(response, ctx)

    def _build_output(self, response, ctx: BatchTaskContext) -> EngineOutput:
        segments: list[Segment] = []

        for idx, result in enumerate(response.results):
            if not result.alternatives:
                continue
            alt = result.alternatives[0]
            words = [
                Word(
                    text=w.word,
                    start=w.start_time,
                    end=w.end_time,
                    confidence=w.confidence,
                )
                for w in alt.words
            ]
            segments.append(
                Segment(
                    id=str(idx),
                    text=alt.transcript,
                    words=words if words else None,
                    start=words[0].start if words else 0.0,
                    end=words[-1].end if words else 0.0,
                    confidence=alt.confidence,
                )
            )

        full_text = " ".join(s.text for s in segments)
        language = ctx.get_metadata("language", "en")
        # Normalize back to short code for Dalston
        if "-" in language:
            language = language.split("-")[0]

        payload = TranscribeOutput(
            text=full_text,
            segments=segments,
            language=language,
            alignment_method=AlignmentMethod.CTC,
            timestamp_granularity_requested=TimestampGranularity.WORD,
            timestamp_granularity_actual=TimestampGranularity.WORD,
            runtime=ctx.runtime,
        )

        return EngineOutput(data=payload)

    def health_check(self) -> dict:
        """Verify gRPC connectivity to Riva NIM."""
        try:
            # A lightweight probe — send empty audio to check connectivity
            config = riva.client.RecognitionConfig(
                language_code="en-US",
                max_alternatives=1,
            )
            self._asr.offline_recognize(b"", config)
            return {"status": "healthy", "riva_connected": True}
        except Exception as e:
            return {"status": "unhealthy", "riva_connected": False, "error": str(e)}


if __name__ == "__main__":
    engine = RivaEngine()
    engine.run()
