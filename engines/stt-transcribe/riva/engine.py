"""Riva NIM batch transcription engine.

Streams audio chunks to a Riva NIM sidecar via gRPC streaming_recognize().
The engine itself is a thin CPU adapter -- all GPU inference runs in the
NIM container.

Supports long recordings without gRPC deadline risk by chunking audio
into configurable segments (DALSTON_RIVA_CHUNK_MS, default 100ms).

Environment variables:
    DALSTON_RIVA_URI: gRPC endpoint for Riva NIM (default: localhost:50051)
    DALSTON_RIVA_CHUNK_MS: Chunk size in milliseconds (default: 100)
    DALSTON_RUNTIME: Runtime identifier for registration (default: riva)
"""

import os
from collections.abc import Iterator
from typing import Any

import grpc
import riva.client
import riva.client.proto.riva_asr_pb2 as riva_asr_pb2

from dalston.common.pipeline_types import (
    Transcript,
    TranscriptSegment,
    TranscriptWord,
)
from dalston.engine_sdk import (
    BatchTaskContext,
    EngineInput,
)
from dalston.engine_sdk.base_transcribe import BaseBatchTranscribeEngine

# Default sample rate for prepared audio
_SAMPLE_RATE = 16000
# Bytes per sample (int16 = 2 bytes)
_BYTES_PER_SAMPLE = 2
# gRPC streaming timeout (2 hours matches max_audio_duration)
_GRPC_TIMEOUT_S = 7200


class RivaBatchEngine(BaseBatchTranscribeEngine):
    """Batch transcription engine using Riva NIM streaming gRPC.

    Reads audio files from disk, streams them in chunks to NIM via
    streaming_recognize(interim_results=False), and collects final
    segments into a Transcript.
    """

    def __init__(self) -> None:
        super().__init__()
        self._uri = os.environ.get("DALSTON_RIVA_URI", "localhost:50051")
        self._chunk_ms = int(os.environ.get("DALSTON_RIVA_CHUNK_MS", "100"))
        self._runtime = os.environ.get("DALSTON_RUNTIME", "riva")

        self._channel = grpc.insecure_channel(self._uri)
        self._asr = riva.client.ASRService(self._channel)

        self.logger.info(
            "engine_init",
            runtime=self._runtime,
            riva_uri=self._uri,
            chunk_ms=self._chunk_ms,
        )

    def _audio_chunk_iter(
        self,
        audio_bytes: bytes,
        config: riva.client.StreamingRecognitionConfig,
    ) -> Iterator[riva.client.StreamingRecognizeRequest]:
        """Yield streaming requests: config first, then audio chunks."""
        # First request carries the config
        yield riva.client.StreamingRecognizeRequest(streaming_config=config)

        chunk_samples = (_SAMPLE_RATE * self._chunk_ms) // 1000
        chunk_bytes = chunk_samples * _BYTES_PER_SAMPLE

        for offset in range(0, len(audio_bytes), chunk_bytes):
            yield riva.client.StreamingRecognizeRequest(
                audio_content=audio_bytes[offset : offset + chunk_bytes]
            )

    def transcribe_audio(
        self, engine_input: EngineInput, ctx: BatchTaskContext
    ) -> Transcript:
        """Transcribe audio via Riva NIM streaming gRPC.

        Args:
            engine_input: Task input with audio file path and config
            ctx: Batch task context for tracing/logging

        Returns:
            Transcript with text, segments, and words
        """
        audio_path = engine_input.audio_path
        params = engine_input.get_transcribe_params()
        language = params.language or "en"

        self.logger.info("transcribing", audio_path=str(audio_path))

        audio_bytes = audio_path.read_bytes()

        streaming_config = riva.client.StreamingRecognitionConfig(
            config=riva.client.RecognitionConfig(
                language_code=language,
                max_alternatives=1,
                enable_word_time_offsets=True,
                enable_automatic_punctuation=True,
                sample_rate_hertz=_SAMPLE_RATE,
                audio_channel_count=1,
            ),
            interim_results=False,
        )

        self._set_runtime_state(status="processing")

        try:
            responses = self._asr.streaming_response_gen(
                audio_chunks=self._audio_chunk_iter(audio_bytes, streaming_config),
                timeout=_GRPC_TIMEOUT_S,
            )

            segments = self._collect_segments(responses, language)

            full_text = " ".join(s.text for s in segments)

            self.logger.info(
                "transcription_complete",
                segment_count=len(segments),
                char_count=len(full_text),
            )

            # Estimate duration from audio bytes (int16 mono 16kHz)
            duration = len(audio_bytes) / (_SAMPLE_RATE * _BYTES_PER_SAMPLE)

            return self.build_transcript(
                text=full_text,
                segments=segments,
                language=language,
                runtime=self._runtime,
                duration=duration,
            )

        finally:
            self._set_runtime_state(status="idle")

    def _collect_segments(
        self,
        responses: Any,
        language: str,
    ) -> list[TranscriptSegment]:
        """Collect final segments from streaming responses.

        Only processes results where is_final=True, ignoring any
        interim results (which shouldn't appear with interim_results=False).
        """
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
                seg_start = 0.0
                seg_end = 0.0

                for w in alt.words:
                    word_start = w.start_time
                    word_end = w.end_time
                    words.append(
                        self.build_word(
                            text=w.word,
                            start=word_start,
                            end=word_end,
                            confidence=w.confidence,
                        )
                    )

                if words:
                    seg_start = words[0].start
                    seg_end = words[-1].end

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

    def get_runtime(self) -> str:
        """Return the runtime identifier."""
        return self._runtime

    def health_check(self) -> dict[str, Any]:
        """Check NIM connectivity via gRPC."""
        try:
            self._asr.stub.GetRivaSpeechRecognitionConfig(
                riva_asr_pb2.RivaSpeechRecognitionConfigRequest()
            )
            return {"status": "healthy", "nim": "connected", "uri": self._uri}
        except grpc.RpcError:
            return {"status": "unhealthy", "nim": "unreachable", "uri": self._uri}

    def shutdown(self) -> None:
        """Close gRPC channel on shutdown."""
        self.logger.info("engine_shutdown")
        if self._channel:
            self._channel.close()


if __name__ == "__main__":
    engine = RivaBatchEngine()
    engine.run()
