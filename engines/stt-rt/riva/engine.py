"""Riva NIM real-time transcription engine.

Uses offline_recognize() via the SessionHandler's periodic re-transcription
pattern. SessionHandler calls transcribe() on accumulated audio periodically
during speech and at utterance boundaries (supports_streaming=True).

Phase 2 can replace this with native gRPC streaming_recognize() where NIM
itself emits incremental partial results.

Environment variables:
    DALSTON_RIVA_URI: gRPC endpoint for Riva NIM (default: localhost:50051)
    DALSTON_ENGINE_ID: Runtime identifier for registration (default: riva)
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

from dalston.common.pipeline_types import (
    AlignmentMethod,
    TranscribeInput,
    Transcript,
    TranscriptWord,
)
from dalston.realtime_sdk.base_transcribe import BaseRealtimeTranscribeEngine

logger = structlog.get_logger()

# Audio parameters matching Dalston's real-time pipeline
_SAMPLE_RATE = 16000

# gRPC timeout for offline_recognize — RT utterances are short (≤30s),
# but allow headroom for NIM cold starts and queuing.
_GRPC_TIMEOUT_S = 120


class RivaRealtimeEngine(BaseRealtimeTranscribeEngine):
    """Real-time transcription engine using Riva NIM gRPC.

    Uses offline_recognize() via the SessionHandler's periodic
    re-transcription pattern. supports_streaming() returns True to
    enable partial results during speech.
    """

    def __init__(self) -> None:
        super().__init__()
        self._uri = os.environ.get("DALSTON_RIVA_URI", "localhost:50051")
        self._engine_id = os.environ.get("DALSTON_ENGINE_ID", "riva")
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

    def transcribe_v1(self, audio: np.ndarray, params: TranscribeInput) -> Transcript:
        """Transcribe an audio segment via Riva NIM.

        Called by SessionHandler when VAD detects an utterance endpoint
        or periodically during speech (when supports_streaming=True).

        Args:
            audio: Audio samples as float32 numpy array, mono, 16kHz
            params: Typed transcriber parameters for this utterance

        Returns:
            Transcript with text, words, language, confidence
        """
        if self._asr is None:
            raise RuntimeError("ASR service not initialized — call load_models() first")

        # Convert float32 [-1.0, 1.0] to int16 bytes for Riva
        audio_int16 = (audio * 32767).astype(np.int16)
        audio_bytes = audio_int16.tobytes()

        lang_code = (
            params.language if params.language and params.language != "auto" else "en"
        )

        config = riva.client.RecognitionConfig(
            language_code=lang_code,
            max_alternatives=1,
            enable_word_time_offsets=True,
            enable_automatic_punctuation=True,
            sample_rate_hertz=_SAMPLE_RATE,
            audio_channel_count=1,
        )

        response = self._asr.offline_recognize(
            audio_bytes, config, timeout=_GRPC_TIMEOUT_S
        )

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
        """NIM manages models — no local model selection."""
        return []

    def get_languages(self) -> list[str]:
        """Languages supported by the NIM model."""
        return ["en", "es", "fr", "de", "it", "pt", "zh", "ja", "ko", "ru"]

    def get_engine_id(self) -> str:
        """Return the engine_id identifier."""
        return self._engine_id

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

    def shutdown(self) -> None:
        """Close gRPC channel on shutdown."""
        logger.info("riva_rt_shutdown")
        if self._channel is not None:
            self._channel.close()
            self._channel = None
            self._asr = None


if __name__ == "__main__":
    import asyncio

    engine = RivaRealtimeEngine()
    asyncio.run(engine.run())
