"""Dalston Realtime SDK for streaming transcription engines.

This SDK provides the foundation for building real-time transcription
workers that integrate with the Dalston real-time infrastructure.

Example usage:
    from dalston.realtime_sdk import RealtimeEngine, TranscribeResult, Word

    class MyEngine(RealtimeEngine):
        def load_models(self):
            from faster_whisper import WhisperModel
            self.model = WhisperModel("large-v3", device="cuda")

        def transcribe(self, audio, language, model_variant):
            segments, info = self.model.transcribe(audio, word_timestamps=True)
            words = []
            text_parts = []
            for segment in segments:
                text_parts.append(segment.text)
                for w in segment.words or []:
                    words.append(Word(w.word, w.start, w.end, w.probability))
            return TranscribeResult(
                text=" ".join(text_parts),
                words=words,
                language=info.language,
                confidence=info.language_probability,
            )

    if __name__ == "__main__":
        import asyncio
        engine = MyEngine()
        asyncio.run(engine.run())

Environment variables:
    WORKER_ID: Unique identifier for this worker
    WORKER_PORT: WebSocket server port (default: 9000)
    MAX_SESSIONS: Maximum concurrent sessions (default: 4)
    REDIS_URL: Redis connection URL (default: redis://localhost:6379)
"""

# Light imports (no heavy dependencies like numpy, torch, websockets)
from dalston.realtime_sdk.assembler import (
    Segment,
    TranscribeResult,
    TranscriptAssembler,
    Word,
)
from dalston.realtime_sdk.protocol import (
    ErrorCode,
    ErrorMessage,
    SegmentInfo,
    SessionBeginMessage,
    SessionConfigInfo,
    SessionEndMessage,
    TranscriptFinalMessage,
    TranscriptPartialMessage,
    VADSpeechEndMessage,
    VADSpeechStartMessage,
    WordInfo,
)


def __getattr__(name: str):
    """Lazy import for heavy dependencies (numpy, torch, websockets, redis).

    This allows importing light components (protocol, assembler) without
    requiring all heavy dependencies to be installed.
    """
    # Heavy imports that require numpy, torch, websockets, redis
    if name == "RealtimeEngine":
        from dalston.realtime_sdk.base import RealtimeEngine

        return RealtimeEngine
    elif name == "WorkerRegistry":
        from dalston.realtime_sdk.registry import WorkerRegistry

        return WorkerRegistry
    elif name == "WorkerInfo":
        from dalston.realtime_sdk.registry import WorkerInfo

        return WorkerInfo
    elif name == "SessionHandler":
        from dalston.realtime_sdk.session import SessionHandler

        return SessionHandler
    elif name == "SessionConfig":
        from dalston.realtime_sdk.session import SessionConfig

        return SessionConfig
    elif name == "AudioBuffer":
        from dalston.realtime_sdk.session import AudioBuffer

        return AudioBuffer
    elif name == "VADProcessor":
        from dalston.realtime_sdk.vad import VADProcessor

        return VADProcessor
    elif name == "VADConfig":
        from dalston.realtime_sdk.vad import VADConfig

        return VADConfig
    elif name == "VADResult":
        from dalston.realtime_sdk.vad import VADResult

        return VADResult
    elif name == "VADState":
        from dalston.realtime_sdk.vad import VADState

        return VADState

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    # Core engine (lazy)
    "RealtimeEngine",
    "TranscribeResult",
    # Session handling (lazy)
    "SessionHandler",
    "SessionConfig",
    "AudioBuffer",
    # VAD (lazy)
    "VADProcessor",
    "VADConfig",
    "VADResult",
    "VADState",
    # Transcript assembly
    "TranscriptAssembler",
    "Segment",
    "Word",
    # Worker registry (lazy)
    "WorkerRegistry",
    "WorkerInfo",
    # Protocol messages
    "SessionBeginMessage",
    "SessionEndMessage",
    "TranscriptPartialMessage",
    "TranscriptFinalMessage",
    "VADSpeechStartMessage",
    "VADSpeechEndMessage",
    "ErrorMessage",
    "ErrorCode",
    # Protocol types
    "SessionConfigInfo",
    "SegmentInfo",
    "WordInfo",
]
