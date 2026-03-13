"""Riva NIM batch transcription adapter.

Streams audio chunks to a Riva NIM sidecar via gRPC streaming_recognize().
Delegates gRPC communication to the shared RivaClient.

When run standalone, creates its own RivaClient.  When used within the
unified runner, accepts an injected client to share a single gRPC channel
with the RT adapter.
"""

from __future__ import annotations

import os
from typing import Any

from riva_client import BYTES_PER_SAMPLE, SAMPLE_RATE, RivaClient

from dalston.common.pipeline_types import (
    Transcript,
    TranscriptSegment,
    TranscriptWord,
)
from dalston.engine_sdk import (
    BatchTaskContext,
    EngineCapabilities,
    EngineInput,
)
from dalston.engine_sdk.base_transcribe import BaseBatchTranscribeEngine


class RivaBatchEngine(BaseBatchTranscribeEngine):
    """Batch transcription engine using Riva NIM streaming gRPC.

    Reads audio files from disk, streams them in chunks to NIM via
    streaming_recognize(interim_results=False), and collects final
    segments into a Transcript.
    """

    def __init__(self, core: RivaClient | None = None) -> None:
        super().__init__()
        self._core = core if core is not None else RivaClient.from_env()
        self._engine_id = os.environ.get("DALSTON_ENGINE_ID", "riva")

        self.logger.info(
            "engine_init",
            engine_id=self._engine_id,
            riva_uri=self._core.uri,
            chunk_ms=self._core.chunk_ms,
            shared_core=core is not None,
        )

    def transcribe_audio(
        self, engine_input: EngineInput, ctx: BatchTaskContext
    ) -> Transcript:
        """Transcribe audio via Riva NIM streaming gRPC."""
        audio_path = engine_input.audio_path
        params = engine_input.get_transcribe_params()
        language = params.language or "en"

        self.logger.info("transcribing", audio_path=str(audio_path))

        audio_bytes = audio_path.read_bytes()

        self._set_runtime_state(status="processing")

        try:
            responses = self._core.streaming_recognize(audio_bytes, language)
            segments = self._collect_segments(responses, language)
            full_text = " ".join(s.text for s in segments)

            self.logger.info(
                "transcription_complete",
                segment_count=len(segments),
                char_count=len(full_text),
            )

            duration = len(audio_bytes) / (SAMPLE_RATE * BYTES_PER_SAMPLE)

            return self.build_transcript(
                text=full_text,
                segments=segments,
                language=language,
                engine_id=self._engine_id,
                duration=duration,
            )
        finally:
            self._set_runtime_state(status="idle")

    def _collect_segments(
        self,
        responses: Any,
        language: str,
    ) -> list[TranscriptSegment]:
        """Collect final segments from streaming responses."""
        segments: list[TranscriptSegment] = []

        for response in responses:
            for result in response.results:
                if not result.is_final:
                    continue
                if not result.alternatives:
                    continue

                alt = result.alternatives[0]
                transcript = alt.transcript.strip()
                if not transcript:
                    continue

                words: list[TranscriptWord] = []
                for w in alt.words:
                    words.append(
                        self.build_word(
                            text=w.word,
                            start=w.start_time,
                            end=w.end_time,
                            confidence=w.confidence,
                        )
                    )

                seg_start = words[0].start if words else 0.0
                seg_end = words[-1].end if words else 0.0

                segments.append(
                    self.build_segment(
                        start=seg_start,
                        end=seg_end,
                        text=transcript,
                        words=words,
                        confidence=alt.confidence,
                        language=language,
                    )
                )

        return segments

    def get_engine_id(self) -> str:
        return self._engine_id

    def health_check(self) -> dict[str, Any]:
        nim_health = self._core.health_check()
        status = "healthy" if nim_health["nim"] == "connected" else "unhealthy"
        return {"status": status, **nim_health}

    def get_capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            engine_id=self._engine_id,
            version="1.1.0",
            stages=["transcribe"],
            supports_streaming=False,
            model_variants=[],
            gpu_required=False,
            gpu_vram_mb=0,
            supports_cpu=True,
            min_ram_gb=1,
            rtf_gpu=0.05,
            rtf_cpu=0.05,
        )

    def shutdown(self) -> None:
        self.logger.info("engine_shutdown")


if __name__ == "__main__":
    engine = RivaBatchEngine()
    engine.run()
