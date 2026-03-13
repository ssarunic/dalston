"""Shared Riva NIM gRPC client.

Wraps a single gRPC channel and ASRService instance, shared between the
batch and realtime adapters in the unified runner.  All GPU inference runs
in the Riva NIM sidecar -- this client is a lightweight connection handle.

Environment variables:
    DALSTON_RIVA_URI: gRPC endpoint for Riva NIM (default: localhost:50051)
    DALSTON_RIVA_CHUNK_MS: Chunk size in milliseconds for batch streaming (default: 100)
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import grpc
import riva.client
import riva.client.proto.riva_asr_pb2 as riva_asr_pb2
import structlog

logger = structlog.get_logger()

# Audio constants for Dalston's prepared audio format
SAMPLE_RATE = 16000
BYTES_PER_SAMPLE = 2  # int16

# gRPC streaming timeout (2 hours matches max_audio_duration)
GRPC_STREAM_TIMEOUT_S = 7200

# gRPC timeout for offline_recognize (RT utterances are short)
GRPC_OFFLINE_TIMEOUT_S = 120

# Languages supported by Riva NIM
SUPPORTED_LANGUAGES = ["en", "es", "fr", "de", "it", "pt", "zh", "ja", "ko", "ru"]

# Default boost score for vocabulary word boosting (range 0-100).
# NVIDIA recommends 20-100 for most use cases.
VOCABULARY_BOOST_SCORE = 20.0


class RivaClient:
    """Shared gRPC client for Riva NIM.

    Creates and owns a single gRPC channel.  Both batch and RT adapters
    call methods on this client instead of managing their own channels.
    """

    def __init__(self) -> None:
        self.uri = os.environ.get("DALSTON_RIVA_URI", "localhost:50051")
        self.chunk_ms = int(os.environ.get("DALSTON_RIVA_CHUNK_MS", "100"))

        self._channel = grpc.insecure_channel(self.uri)
        self.asr = riva.client.ASRService(self._channel)

        logger.info(
            "riva_client_init",
            uri=self.uri,
            chunk_ms=self.chunk_ms,
        )

    @classmethod
    def from_env(cls) -> RivaClient:
        """Create a RivaClient from environment variables."""
        return cls()

    def _build_recognition_config(
        self,
        language: str = "en",
        vocabulary: list[str] | None = None,
    ) -> riva.client.RecognitionConfig:
        """Build a RecognitionConfig with optional word boosting.

        Args:
            language: Language code.
            vocabulary: Optional list of terms to boost recognition.
                Uses Riva's SpeechContext with a default boost score of 20.
        """
        config = riva.client.RecognitionConfig(
            language_code=language,
            max_alternatives=1,
            enable_word_time_offsets=True,
            enable_automatic_punctuation=True,
            sample_rate_hertz=SAMPLE_RATE,
            audio_channel_count=1,
        )

        if vocabulary:
            riva.client.add_word_boosting_to_config(
                config, vocabulary, VOCABULARY_BOOST_SCORE
            )
            logger.debug(
                "vocabulary_boosting_enabled",
                terms_count=len(vocabulary),
                boost_score=VOCABULARY_BOOST_SCORE,
            )

        return config

    def streaming_recognize(
        self,
        audio_bytes: bytes,
        language: str = "en",
        vocabulary: list[str] | None = None,
    ) -> Any:
        """Stream audio chunks to NIM via streaming_recognize().

        Used by the batch adapter for robust processing of long recordings
        without gRPC deadline risk.

        Args:
            audio_bytes: Raw PCM audio bytes (int16, 16kHz mono).
            language: Language code.
            vocabulary: Optional list of terms to boost recognition.
        """
        config = self._build_recognition_config(language, vocabulary)
        streaming_config = riva.client.StreamingRecognitionConfig(
            config=config,
            interim_results=False,
        )

        return self.asr.streaming_response_gen(
            audio_chunks=self._audio_chunk_iter(audio_bytes, streaming_config),
            timeout=GRPC_STREAM_TIMEOUT_S,
        )

    def offline_recognize(
        self,
        audio_bytes: bytes,
        language: str = "en",
        vocabulary: list[str] | None = None,
    ) -> Any:
        """Transcribe audio via offline_recognize().

        Used by the RT adapter for VAD-chunked utterances.

        Args:
            audio_bytes: Raw PCM audio bytes (int16, 16kHz mono).
            language: Language code.
            vocabulary: Optional list of terms to boost recognition.
        """
        config = self._build_recognition_config(language, vocabulary)

        return self.asr.offline_recognize(
            audio_bytes, config, timeout=GRPC_OFFLINE_TIMEOUT_S
        )

    def _audio_chunk_iter(
        self,
        audio_bytes: bytes,
        config: riva.client.StreamingRecognitionConfig,
    ) -> Iterator[riva.client.StreamingRecognizeRequest]:
        """Yield streaming requests: config first, then audio chunks."""
        yield riva.client.StreamingRecognizeRequest(streaming_config=config)

        chunk_samples = (SAMPLE_RATE * self.chunk_ms) // 1000
        chunk_bytes = chunk_samples * BYTES_PER_SAMPLE

        for offset in range(0, len(audio_bytes), chunk_bytes):
            yield riva.client.StreamingRecognizeRequest(
                audio_content=audio_bytes[offset : offset + chunk_bytes]
            )

    def health_check(self) -> dict[str, Any]:
        """Check NIM connectivity via gRPC."""
        try:
            self.asr.stub.GetRivaSpeechRecognitionConfig(
                riva_asr_pb2.RivaSpeechRecognitionConfigRequest()
            )
            return {"nim": "connected", "uri": self.uri}
        except grpc.RpcError:
            return {"nim": "unreachable", "uri": self.uri}

    def shutdown(self) -> None:
        """Close the gRPC channel."""
        logger.info("riva_client_shutdown")
        if self._channel:
            self._channel.close()
