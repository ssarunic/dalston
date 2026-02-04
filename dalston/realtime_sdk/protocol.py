"""WebSocket protocol message types for real-time transcription.

Defines all message types exchanged between client and server
following the Dalston native WebSocket protocol.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

# -----------------------------------------------------------------------------
# Shared Types
# -----------------------------------------------------------------------------


@dataclass
class WordInfo:
    """Word with timing and confidence."""

    word: str
    start: float
    end: float
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SegmentInfo:
    """Transcript segment summary."""

    start: float
    end: float
    text: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SessionConfigInfo:
    """Session configuration echoed back to client."""

    sample_rate: int
    encoding: str
    channels: int
    language: str
    model: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# -----------------------------------------------------------------------------
# Server → Client Messages
# -----------------------------------------------------------------------------


@dataclass
class SessionBeginMessage:
    """Sent when session is established."""

    session_id: str
    config: SessionConfigInfo
    type: str = field(default="session.begin", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "session_id": self.session_id,
            "config": self.config.to_dict(),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


@dataclass
class TranscriptPartialMessage:
    """Interim transcript result (may change)."""

    text: str
    start: float
    end: float
    type: str = field(default="transcript.partial", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "text": self.text,
            "start": self.start,
            "end": self.end,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


@dataclass
class TranscriptFinalMessage:
    """Final transcript for completed utterance."""

    text: str
    start: float
    end: float
    confidence: float
    words: list[WordInfo] | None = None
    type: str = field(default="transcript.final", init=False)

    def to_dict(self) -> dict[str, Any]:
        result = {
            "type": self.type,
            "text": self.text,
            "start": self.start,
            "end": self.end,
            "confidence": self.confidence,
        }
        if self.words is not None:
            result["words"] = [w.to_dict() for w in self.words]
        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


@dataclass
class VADSpeechStartMessage:
    """Voice activity detected - speech started."""

    timestamp: float
    type: str = field(default="vad.speech_start", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "timestamp": self.timestamp,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


@dataclass
class VADSpeechEndMessage:
    """Voice activity ended - speech stopped."""

    timestamp: float
    type: str = field(default="vad.speech_end", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "timestamp": self.timestamp,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


@dataclass
class SessionEndMessage:
    """Sent when session ends (gracefully or due to timeout)."""

    session_id: str
    total_duration: float
    total_speech_duration: float
    transcript: str
    segments: list[SegmentInfo]
    enhancement_job_id: str | None = None
    type: str = field(default="session.end", init=False)

    def to_dict(self) -> dict[str, Any]:
        result = {
            "type": self.type,
            "session_id": self.session_id,
            "total_duration": self.total_duration,
            "total_speech_duration": self.total_speech_duration,
            "transcript": self.transcript,
            "segments": [s.to_dict() for s in self.segments],
        }
        if self.enhancement_job_id is not None:
            result["enhancement_job_id"] = self.enhancement_job_id
        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


@dataclass
class ErrorMessage:
    """Error message with optional recovery hint."""

    code: str
    message: str
    recoverable: bool = True
    type: str = field(default="error", init=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "code": self.code,
            "message": self.message,
            "recoverable": self.recoverable,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


# -----------------------------------------------------------------------------
# Client → Server Messages (parsed from JSON)
# -----------------------------------------------------------------------------


@dataclass
class ConfigUpdateMessage:
    """Client request to update session configuration."""

    language: str | None = None
    type: str = field(default="config", init=False)


@dataclass
class FlushMessage:
    """Client request to flush buffered audio."""

    type: str = field(default="flush", init=False)


@dataclass
class EndMessage:
    """Client request to end session gracefully."""

    type: str = field(default="end", init=False)


# Type alias for all client messages
ClientMessage = ConfigUpdateMessage | FlushMessage | EndMessage

# Type alias for all server messages
ServerMessage = (
    SessionBeginMessage
    | TranscriptPartialMessage
    | TranscriptFinalMessage
    | VADSpeechStartMessage
    | VADSpeechEndMessage
    | SessionEndMessage
    | ErrorMessage
)


def parse_client_message(data: str | dict) -> ClientMessage:
    """Parse incoming client message from JSON string or dict.

    Args:
        data: JSON string or already-parsed dict

    Returns:
        Parsed client message

    Raises:
        ValueError: If message type is unknown or invalid
    """
    if isinstance(data, str):
        try:
            parsed = json.loads(data)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {e}") from e
    else:
        parsed = data

    if not isinstance(parsed, dict):
        raise ValueError("Message must be a JSON object")

    msg_type = parsed.get("type")

    if msg_type == "config":
        return ConfigUpdateMessage(language=parsed.get("language"))
    elif msg_type == "flush":
        return FlushMessage()
    elif msg_type == "end":
        return EndMessage()
    else:
        raise ValueError(f"Unknown message type: {msg_type}")


# Error codes
class ErrorCode:
    """Standard error codes for ErrorMessage."""

    RATE_LIMIT = "rate_limit"
    INVALID_AUDIO = "invalid_audio"
    INVALID_MESSAGE = "invalid_message"
    LANGUAGE_UNSUPPORTED = "language_unsupported"
    NO_CAPACITY = "no_capacity"
    SESSION_TIMEOUT = "session_timeout"
    INTERNAL_ERROR = "internal_error"
