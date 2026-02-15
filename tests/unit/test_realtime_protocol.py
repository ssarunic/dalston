"""Unit tests for realtime_sdk protocol module."""

import json

import pytest

from dalston.realtime_sdk.protocol import (
    ConfigUpdateMessage,
    EndMessage,
    ErrorCode,
    ErrorMessage,
    FlushMessage,
    SegmentInfo,
    SessionBeginMessage,
    SessionConfigInfo,
    SessionEndMessage,
    TranscriptFinalMessage,
    TranscriptPartialMessage,
    VADSpeechEndMessage,
    VADSpeechStartMessage,
    WordInfo,
    parse_client_message,
)


class TestWordInfo:
    """Tests for WordInfo dataclass."""

    def test_to_dict(self):
        word = WordInfo(word="hello", start=0.0, end=0.5, confidence=0.95)
        result = word.to_dict()

        assert result == {
            "word": "hello",
            "start": 0.0,
            "end": 0.5,
            "confidence": 0.95,
        }


class TestSegmentInfo:
    """Tests for SegmentInfo dataclass."""

    def test_to_dict(self):
        segment = SegmentInfo(start=0.0, end=2.5, text="Hello world")
        result = segment.to_dict()

        assert result == {
            "start": 0.0,
            "end": 2.5,
            "text": "Hello world",
        }


class TestSessionConfigInfo:
    """Tests for SessionConfigInfo dataclass."""

    def test_to_dict(self):
        config = SessionConfigInfo(
            sample_rate=16000,
            encoding="pcm_s16le",
            channels=1,
            language="en",
            model="fast",
        )
        result = config.to_dict()

        assert result == {
            "sample_rate": 16000,
            "encoding": "pcm_s16le",
            "channels": 1,
            "language": "en",
            "model": "fast",
        }


class TestSessionBeginMessage:
    """Tests for SessionBeginMessage."""

    def test_to_dict(self):
        config = SessionConfigInfo(
            sample_rate=16000,
            encoding="pcm_s16le",
            channels=1,
            language="auto",
            model="fast",
        )
        msg = SessionBeginMessage(session_id="sess_abc123", config=config)

        result = msg.to_dict()

        assert result["type"] == "session.begin"
        assert result["session_id"] == "sess_abc123"
        assert result["config"]["sample_rate"] == 16000
        assert result["config"]["language"] == "auto"

    def test_to_json(self):
        config = SessionConfigInfo(
            sample_rate=16000,
            encoding="pcm_s16le",
            channels=1,
            language="en",
            model="accurate",
        )
        msg = SessionBeginMessage(session_id="sess_123", config=config)

        json_str = msg.to_json()
        parsed = json.loads(json_str)

        assert parsed["type"] == "session.begin"
        assert parsed["session_id"] == "sess_123"


class TestTranscriptPartialMessage:
    """Tests for TranscriptPartialMessage."""

    def test_to_dict(self):
        msg = TranscriptPartialMessage(text="Hello wor", start=0.0, end=1.5)

        result = msg.to_dict()

        assert result["type"] == "transcript.partial"
        assert result["text"] == "Hello wor"
        assert result["start"] == 0.0
        assert result["end"] == 1.5

    def test_to_json(self):
        msg = TranscriptPartialMessage(text="test", start=0.5, end=1.0)
        json_str = msg.to_json()

        assert json.loads(json_str)["type"] == "transcript.partial"


class TestTranscriptFinalMessage:
    """Tests for TranscriptFinalMessage."""

    def test_to_dict_without_words(self):
        msg = TranscriptFinalMessage(
            text="Hello world",
            start=0.0,
            end=2.0,
            confidence=0.95,
        )

        result = msg.to_dict()

        assert result["type"] == "transcript.final"
        assert result["text"] == "Hello world"
        assert result["start"] == 0.0
        assert result["end"] == 2.0
        assert result["confidence"] == 0.95
        assert "words" not in result

    def test_to_dict_with_words(self):
        words = [
            WordInfo(word="Hello", start=0.0, end=0.8, confidence=0.97),
            WordInfo(word="world", start=1.0, end=1.8, confidence=0.93),
        ]
        msg = TranscriptFinalMessage(
            text="Hello world",
            start=0.0,
            end=2.0,
            confidence=0.95,
            words=words,
        )

        result = msg.to_dict()

        assert "words" in result
        assert len(result["words"]) == 2
        assert result["words"][0]["word"] == "Hello"
        assert result["words"][1]["word"] == "world"

    def test_to_json(self):
        msg = TranscriptFinalMessage(
            text="Test",
            start=0.0,
            end=1.0,
            confidence=0.9,
        )
        json_str = msg.to_json()

        assert json.loads(json_str)["type"] == "transcript.final"


class TestVADMessages:
    """Tests for VAD event messages."""

    def test_speech_start_message(self):
        msg = VADSpeechStartMessage(timestamp=5.5)

        result = msg.to_dict()

        assert result["type"] == "vad.speech_start"
        assert result["timestamp"] == 5.5

    def test_speech_end_message(self):
        msg = VADSpeechEndMessage(timestamp=8.2)

        result = msg.to_dict()

        assert result["type"] == "vad.speech_end"
        assert result["timestamp"] == 8.2

    def test_to_json(self):
        msg = VADSpeechStartMessage(timestamp=1.0)
        json_str = msg.to_json()

        assert json.loads(json_str)["type"] == "vad.speech_start"


class TestSessionEndMessage:
    """Tests for SessionEndMessage."""

    def test_to_dict_basic(self):
        segments = [
            SegmentInfo(start=0.0, end=2.0, text="Hello"),
            SegmentInfo(start=3.0, end=5.0, text="World"),
        ]
        msg = SessionEndMessage(
            session_id="sess_abc",
            total_audio_seconds=10.0,
            total_speech_duration=4.0,
            transcript="Hello World",
            segments=segments,
        )

        result = msg.to_dict()

        assert result["type"] == "session.end"
        assert result["session_id"] == "sess_abc"
        assert result["total_audio_seconds"] == 10.0
        assert result["total_speech_duration"] == 4.0
        assert result["transcript"] == "Hello World"
        assert len(result["segments"]) == 2
        assert "enhancement_job_id" not in result

    def test_to_dict_with_enhancement_job(self):
        msg = SessionEndMessage(
            session_id="sess_abc",
            total_audio_seconds=10.0,
            total_speech_duration=4.0,
            transcript="Hello",
            segments=[],
            enhancement_job_id="job_xyz789",
        )

        result = msg.to_dict()

        assert result["enhancement_job_id"] == "job_xyz789"


class TestErrorMessage:
    """Tests for ErrorMessage."""

    def test_to_dict_recoverable(self):
        msg = ErrorMessage(
            code=ErrorCode.INVALID_AUDIO,
            message="Audio format not supported",
            recoverable=True,
        )

        result = msg.to_dict()

        assert result["type"] == "error"
        assert result["code"] == "invalid_audio"
        assert result["message"] == "Audio format not supported"
        assert result["recoverable"] is True

    def test_to_dict_non_recoverable(self):
        msg = ErrorMessage(
            code=ErrorCode.INTERNAL_ERROR,
            message="Server error",
            recoverable=False,
        )

        result = msg.to_dict()

        assert result["recoverable"] is False

    def test_error_codes(self):
        assert ErrorCode.RATE_LIMIT == "rate_limit"
        assert ErrorCode.INVALID_AUDIO == "invalid_audio"
        assert ErrorCode.INVALID_MESSAGE == "invalid_message"
        assert ErrorCode.NO_CAPACITY == "no_capacity"
        assert ErrorCode.SESSION_TIMEOUT == "session_timeout"
        assert ErrorCode.INTERNAL_ERROR == "internal_error"


class TestParseClientMessage:
    """Tests for parse_client_message function."""

    def test_parse_config_message(self):
        msg = parse_client_message('{"type": "config", "language": "es"}')

        assert isinstance(msg, ConfigUpdateMessage)
        assert msg.language == "es"
        assert msg.type == "config"

    def test_parse_config_message_without_language(self):
        msg = parse_client_message('{"type": "config"}')

        assert isinstance(msg, ConfigUpdateMessage)
        assert msg.language is None

    def test_parse_flush_message(self):
        msg = parse_client_message('{"type": "flush"}')

        assert isinstance(msg, FlushMessage)
        assert msg.type == "flush"

    def test_parse_end_message(self):
        msg = parse_client_message('{"type": "end"}')

        assert isinstance(msg, EndMessage)
        assert msg.type == "end"

    def test_parse_from_dict(self):
        msg = parse_client_message({"type": "end"})

        assert isinstance(msg, EndMessage)

    def test_parse_invalid_json(self):
        with pytest.raises(ValueError, match="Invalid JSON"):
            parse_client_message("not valid json")

    def test_parse_non_object(self):
        with pytest.raises(ValueError, match="must be a JSON object"):
            parse_client_message("[]")

    def test_parse_unknown_type(self):
        with pytest.raises(ValueError, match="Unknown message type"):
            parse_client_message('{"type": "unknown"}')

    def test_parse_missing_type(self):
        with pytest.raises(ValueError, match="Unknown message type"):
            parse_client_message('{"foo": "bar"}')
