"""Unit tests for realtime session configuration parsing."""

import json

import pytest

from dalston.realtime_sdk.base import RealtimeEngine
from dalston.realtime_sdk.session import SessionConfig


class MockRealtimeEngine(RealtimeEngine):
    """Mock engine for testing _parse_connection_params."""

    def load_models(self) -> None:
        pass

    def transcribe(self, audio, language, model_variant, vocabulary=None):
        pass


class TestSessionConfigParsing:
    """Tests for parsing session config from connection parameters."""

    @pytest.fixture
    def engine(self):
        """Create a mock engine for testing."""
        return MockRealtimeEngine()

    def test_parse_basic_params(self, engine):
        """Test parsing basic session parameters."""
        path = "/session?language=en&sample_rate=16000"
        config = engine._parse_connection_params(path)

        assert config.language == "en"
        assert config.sample_rate == 16000
        assert config.vocabulary is None

    def test_parse_vocabulary_valid_json(self, engine):
        """Test parsing valid vocabulary JSON array."""
        vocab = ["PostgreSQL", "Kubernetes", "FastAPI"]
        path = f"/session?vocabulary={json.dumps(vocab)}"
        config = engine._parse_connection_params(path)

        assert config.vocabulary == vocab

    def test_parse_vocabulary_empty_array(self, engine):
        """Test that empty vocabulary array results in None."""
        path = "/session?vocabulary=[]"
        config = engine._parse_connection_params(path)

        assert config.vocabulary is None

    def test_parse_vocabulary_invalid_json(self, engine):
        """Test that invalid JSON is ignored."""
        path = "/session?vocabulary=not_valid_json"
        config = engine._parse_connection_params(path)

        assert config.vocabulary is None

    def test_parse_vocabulary_non_array(self, engine):
        """Test that non-array JSON is ignored."""
        path = '/session?vocabulary="just a string"'
        config = engine._parse_connection_params(path)

        assert config.vocabulary is None

    def test_parse_vocabulary_non_string_items(self, engine):
        """Test that array with non-string items is ignored."""
        path = "/session?vocabulary=[1, 2, 3]"
        config = engine._parse_connection_params(path)

        assert config.vocabulary is None

    def test_parse_vocabulary_missing(self, engine):
        """Test that missing vocabulary parameter results in None."""
        path = "/session?language=en"
        config = engine._parse_connection_params(path)

        assert config.vocabulary is None

    def test_parse_vocabulary_url_encoded(self, engine):
        """Test parsing URL-encoded vocabulary parameter."""
        from urllib.parse import urlencode

        vocab = ["PostgreSQL", "Kubernetes"]
        params = urlencode({"vocabulary": json.dumps(vocab)})
        path = f"/session?{params}"
        config = engine._parse_connection_params(path)

        assert config.vocabulary == vocab

    def test_parse_full_config(self, engine):
        """Test parsing full session config with vocabulary."""
        from urllib.parse import urlencode

        vocab = ["term1", "term2"]
        params = urlencode(
            {
                "session_id": "sess_test123",
                "language": "en",
                "model": "faster-whisper-large-v3",
                "sample_rate": "16000",
                "enable_vad": "true",
                "interim_results": "true",
                "word_timestamps": "true",
                "vocabulary": json.dumps(vocab),
            }
        )
        path = f"/session?{params}"
        config = engine._parse_connection_params(path)

        assert config.session_id == "sess_test123"
        assert config.language == "en"
        assert config.model == "faster-whisper-large-v3"
        assert config.sample_rate == 16000
        assert config.enable_vad is True
        assert config.interim_results is True
        assert config.word_timestamps is True
        assert config.vocabulary == vocab


class TestSessionConfigDataclass:
    """Tests for SessionConfig dataclass."""

    def test_default_vocabulary_is_none(self):
        """Test that default vocabulary is None."""
        config = SessionConfig(session_id="test")
        assert config.vocabulary is None

    def test_vocabulary_can_be_set(self):
        """Test that vocabulary can be set."""
        vocab = ["term1", "term2"]
        config = SessionConfig(session_id="test", vocabulary=vocab)
        assert config.vocabulary == vocab
