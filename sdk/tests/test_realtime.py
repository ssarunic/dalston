"""Tests for real-time transcription client."""

from dalston_sdk import (
    RealtimeMessageType,
    SessionBegin,
    TranscriptFinal,
    TranscriptPartial,
    VADEvent,
)
from dalston_sdk.realtime import _parse_message


class TestParseMessage:
    """Tests for message parsing."""

    def test_parse_session_begin(self):
        """Test parsing session.begin message."""
        data = {
            "type": "session.begin",
            "session_id": "sess-123",
            "model": "faster-whisper-large-v3",
            "language": "en",
            "sample_rate": 16000,
            "encoding": "pcm_s16le",
        }

        message = _parse_message(data)

        assert message.type == RealtimeMessageType.SESSION_BEGIN
        assert isinstance(message.data, SessionBegin)
        assert message.data.session_id == "sess-123"
        assert message.data.model == "faster-whisper-large-v3"
        assert message.data.language == "en"
        assert message.data.sample_rate == 16000

    def test_parse_session_end(self):
        """Test parsing session.end message."""
        data = {
            "type": "session.end",
            "session_id": "sess-123",
            "total_audio_seconds": 10.5,
            "total_billed_seconds": 11.0,
        }

        message = _parse_message(data)

        assert message.type == RealtimeMessageType.SESSION_END
        assert message.data.session_id == "sess-123"
        assert message.data.total_audio_seconds == 10.5
        assert message.data.total_billed_seconds == 11.0

    def test_parse_transcript_partial(self):
        """Test parsing transcript.partial message."""
        data = {
            "type": "transcript.partial",
            "text": "Hello wor",
        }

        message = _parse_message(data)

        assert message.type == RealtimeMessageType.TRANSCRIPT_PARTIAL
        assert isinstance(message.data, TranscriptPartial)
        assert message.data.text == "Hello wor"
        assert message.data.is_final is False

    def test_parse_transcript_final(self):
        """Test parsing transcript.final message."""
        data = {
            "type": "transcript.final",
            "text": "Hello world",
            "start": 0.0,
            "end": 1.0,
            "confidence": 0.95,
            "words": [
                {"text": "Hello", "start": 0.0, "end": 0.5, "confidence": 0.96},
                {"text": "world", "start": 0.6, "end": 1.0, "confidence": 0.94},
            ],
        }

        message = _parse_message(data)

        assert message.type == RealtimeMessageType.TRANSCRIPT_FINAL
        assert isinstance(message.data, TranscriptFinal)
        assert message.data.text == "Hello world"
        assert message.data.start == 0.0
        assert message.data.end == 1.0
        assert message.data.confidence == 0.95
        assert len(message.data.words) == 2
        assert message.data.words[0].text == "Hello"

    def test_parse_transcript_final_without_words(self):
        """Test parsing transcript.final without word-level data."""
        data = {
            "type": "transcript.final",
            "text": "Hello world",
            "start": 0.0,
            "end": 1.0,
        }

        message = _parse_message(data)

        assert message.type == RealtimeMessageType.TRANSCRIPT_FINAL
        assert message.data.words is None

    def test_parse_vad_speech_start(self):
        """Test parsing vad.speech_start message."""
        data = {
            "type": "vad.speech_start",
            "timestamp": 1.5,
        }

        message = _parse_message(data)

        assert message.type == RealtimeMessageType.VAD_SPEECH_START
        assert isinstance(message.data, VADEvent)
        assert message.data.type == "speech_start"
        assert message.data.timestamp == 1.5

    def test_parse_vad_speech_end(self):
        """Test parsing vad.speech_end message."""
        data = {
            "type": "vad.speech_end",
            "timestamp": 3.0,
        }

        message = _parse_message(data)

        assert message.type == RealtimeMessageType.VAD_SPEECH_END
        assert message.data.type == "speech_end"
        assert message.data.timestamp == 3.0

    def test_parse_error(self):
        """Test parsing error message."""
        data = {
            "type": "error",
            "code": "invalid_audio",
            "message": "Audio format not supported",
            "details": {"encoding": "unknown"},
        }

        message = _parse_message(data)

        assert message.type == RealtimeMessageType.ERROR
        assert message.data.code == "invalid_audio"
        assert message.data.message == "Audio format not supported"
        assert message.data.details == {"encoding": "unknown"}

    def test_parse_unknown_message(self):
        """Test parsing unknown message type."""
        data = {
            "type": "unknown.message",
            "foo": "bar",
        }

        message = _parse_message(data)

        assert message.type == RealtimeMessageType.ERROR
        assert "Unknown message type" in message.data.message


class TestAsyncRealtimeSession:
    """Tests for AsyncRealtimeSession.

    Note: Full integration tests require a running WebSocket server.
    These tests focus on unit testing the parsing and URL building.
    """

    def test_url_building(self):
        """Test WebSocket URL building."""
        from dalston_sdk import AsyncRealtimeSession

        session = AsyncRealtimeSession(
            base_url="http://localhost:8000",
            language="en",
            model="faster-whisper-large-v3",
            encoding="pcm_f32le",
            sample_rate=44100,
            enable_vad=False,
            interim_results=False,
            word_timestamps=True,
        )

        url = session._build_url()

        assert url.startswith("ws://localhost:8000")
        assert "language=en" in url
        assert "model=faster-whisper-large-v3" in url
        assert "encoding=pcm_f32le" in url
        assert "sample_rate=44100" in url
        assert "enable_vad=false" in url
        assert "interim_results=false" in url
        assert "word_timestamps=true" in url

    def test_http_to_ws_conversion(self):
        """Test HTTP to WS URL conversion."""
        from dalston_sdk import AsyncRealtimeSession

        # http -> ws
        session = AsyncRealtimeSession(base_url="http://localhost:8000")
        assert session.base_url.startswith("ws://")

        # https -> wss
        session = AsyncRealtimeSession(base_url="https://localhost:8000")
        assert session.base_url.startswith("wss://")

        # ws stays ws
        session = AsyncRealtimeSession(base_url="ws://localhost:8000")
        assert session.base_url.startswith("ws://")

    def test_headers_with_api_key(self):
        """Test headers include API key when provided."""
        from dalston_sdk import AsyncRealtimeSession

        session = AsyncRealtimeSession(
            base_url="ws://localhost:8000",
            api_key="test-key",
        )

        headers = session._build_headers()

        assert headers["Authorization"] == "Bearer test-key"

    def test_headers_without_api_key(self):
        """Test headers are empty without API key."""
        from dalston_sdk import AsyncRealtimeSession

        session = AsyncRealtimeSession(base_url="ws://localhost:8000")

        headers = session._build_headers()

        assert headers == {}


class TestRealtimeSession:
    """Tests for synchronous RealtimeSession."""

    def test_callback_registration(self):
        """Test callback registration."""
        from dalston_sdk import RealtimeSession, TranscriptFinal

        session = RealtimeSession(base_url="ws://localhost:8000")
        received = []

        @session.on_final
        def handle_final(transcript: TranscriptFinal):
            received.append(transcript)

        assert len(session._callbacks["final"]) == 1

    def test_multiple_callbacks(self):
        """Test multiple callbacks for same event."""
        from dalston_sdk import RealtimeSession

        session = RealtimeSession(base_url="ws://localhost:8000")

        @session.on_final
        def handle1(t):
            pass

        @session.on_final
        def handle2(t):
            pass

        assert len(session._callbacks["final"]) == 2

    def test_all_callback_types(self):
        """Test all callback registration methods."""
        from dalston_sdk import RealtimeSession

        session = RealtimeSession(base_url="ws://localhost:8000")

        @session.on_partial
        def h1(t):
            pass

        @session.on_final
        def h2(t):
            pass

        @session.on_vad_start
        def h3(e):
            pass

        @session.on_vad_end
        def h4(e):
            pass

        @session.on_error
        def h5(e):
            pass

        assert len(session._callbacks["partial"]) == 1
        assert len(session._callbacks["final"]) == 1
        assert len(session._callbacks["vad_start"]) == 1
        assert len(session._callbacks["vad_end"]) == 1
        assert len(session._callbacks["error"]) == 1
