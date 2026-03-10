"""Riva NIM real-time transcription engine.

Accepts WebSocket sessions, pipes audio chunks to a Riva NIM sidecar via
gRPC streaming_recognize(interim_results=True), and forwards partial and
final transcript events back to the client.

In Phase 1 (supports_streaming=True with offline_recognize pattern),
SessionHandler calls transcribe() on accumulated audio periodically
during speech and at utterance boundaries.

Phase 2 can replace this with native gRPC streaming where NIM itself
emits incremental partial results.

Environment variables:
    DALSTON_RIVA_URI: gRPC endpoint for Riva NIM (default: localhost:50051)
    DALSTON_RUNTIME: Runtime identifier for registration (default: riva)
    DALSTON_INSTANCE: Unique worker identifier (required)
    DALSTON_WORKER_PORT: WebSocket port (default: 9000)
    DALSTON_MAX_SESSIONS: Max concurrent sessions (default: 8)
"""

import os
from typing import Any

import grpc
import numpy as np
import riva.client
import riva.client.proto.riva_asr_pb2 as riva_asr_pb2
import structlog

from dalston.realtime_sdk import RealtimeEngine, TranscribeResult, Word

logger = structlog.get_logger()

# Audio parameters matching Dalston's real-time pipeline
_SAMPLE_RATE = 16000


class RivaRealtimeEngine(RealtimeEngine):
    """Real-time transcription engine using Riva NIM gRPC.

    Uses offline_recognize() via the SessionHandler's periodic
    re-transcription pattern. supports_streaming() returns True to
    enable partial results during speech.
    """

    def __init__(self) -> None:
        super().__init__()
        self._uri = os.environ.get("DALSTON_RIVA_URI", "localhost:50051")
        self._runtime = os.environ.get("DALSTON_RUNTIME", "riva")
        self._channel: grpc.Channel | None = None
        self._asr: riva.client.ASRService | None = None

    def load_models(self) -> None:
        """Initialize gRPC connection to NIM sidecar.

        No local models are loaded — all inference runs in the NIM container.
        """
        logger.info("riva_nim_connecting", uri=self._uri)
        self._channel = grpc.insecure_channel(self._uri)
        self._asr = riva.client.ASRService(self._channel)
        logger.info("riva_nim_channel_ready", uri=self._uri)

    def transcribe(
        self,
        audio: np.ndarray,
        language: str,
        model_variant: str,
        vocabulary: list[str] | None = None,
    ) -> TranscribeResult:
        """Transcribe an audio segment via Riva NIM.

        Called by SessionHandler when VAD detects an utterance endpoint
        or periodically during speech (when supports_streaming=True).

        Args:
            audio: Audio samples as float32 numpy array, mono, 16kHz
            language: Language code (e.g., "en") or "auto"
            model_variant: Model name (ignored — NIM manages models)
            vocabulary: Not supported yet

        Returns:
            TranscribeResult with text, words, language, confidence
        """
        if self._asr is None:
            raise RuntimeError("ASR service not initialized — call load_models() first")

        # Convert float32 [-1.0, 1.0] to int16 bytes for Riva
        audio_int16 = (audio * 32767).astype(np.int16)
        audio_bytes = audio_int16.tobytes()

        lang_code = language if language and language != "auto" else "en"

        config = riva.client.RecognitionConfig(
            language_code=lang_code,
            max_alternatives=1,
            enable_word_time_offsets=True,
            enable_automatic_punctuation=True,
            sample_rate_hertz=_SAMPLE_RATE,
            audio_channel_count=1,
        )

        response = self._asr.offline_recognize(audio_bytes, config)

        words: list[Word] = []
        text_parts: list[str] = []
        confidence = 0.0

        for result in response.results:
            if not result.alternatives:
                continue
            alt = result.alternatives[0]
            transcript = alt.transcript.strip()
            if transcript:
                text_parts.append(transcript)
            confidence = max(confidence, alt.confidence)

            for w in alt.words:
                words.append(
                    Word(
                        word=w.word,
                        start=w.start_time,
                        end=w.end_time,
                        confidence=alt.confidence,
                    )
                )

        return TranscribeResult(
            text=" ".join(text_parts),
            words=words,
            language=lang_code,
            confidence=confidence,
        )

    def supports_streaming(self) -> bool:
        """Enable partial results via SessionHandler's periodic re-transcription."""
        return True

    def get_models(self) -> list[str]:
        """NIM manages models — no local model selection."""
        return []

    def get_languages(self) -> list[str]:
        """Languages supported by the NIM model."""
        return ["en", "es", "fr", "de", "it", "pt", "zh", "ja", "ko", "ru"]

    def get_runtime(self) -> str:
        """Return the runtime identifier."""
        return "riva"

    def get_supports_vocabulary(self) -> bool:
        """Riva supports boosting but needs config mapping work."""
        return False

    def get_gpu_memory_usage(self) -> str:
        """No local GPU usage — NIM handles GPU."""
        return "0GB"

    def health_check(self) -> dict[str, Any]:
        """Check NIM connectivity via gRPC."""
        base_health = super().health_check()

        nim_status = "unknown"
        if self._asr is not None:
            try:
                self._asr.stub.GetRivaSpeechRecognitionConfig(
                    riva_asr_pb2.RivaSpeechRecognitionConfigRequest()
                )
                nim_status = "connected"
            except grpc.RpcError:
                nim_status = "unreachable"

        return {
            **base_health,
            "nim": nim_status,
            "nim_uri": self._uri,
        }


if __name__ == "__main__":
    import asyncio

    engine = RivaRealtimeEngine()
    asyncio.run(engine.run())
