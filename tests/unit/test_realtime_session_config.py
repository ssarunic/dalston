"""Unit tests for realtime session configuration parsing."""

import json

import pytest

from dalston.common.pipeline_types import TranscribeInput
from dalston.realtime_sdk.base import RealtimeEngine
from dalston.realtime_sdk.session import SessionConfig


class MockRealtimeEngine(RealtimeEngine):
    """Mock engine for testing _parse_connection_params."""

    def load_models(self) -> None:
        pass

    def transcribe(self, audio, params: TranscribeInput):
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
        assert config.lag_warning_seconds == 3.0
        assert config.lag_hard_seconds == 5.0
        assert config.lag_hard_grace_seconds == 2.0
        assert config.debug_chunk_sleep_initial_seconds == 0.0
        assert config.debug_chunk_sleep_increment_seconds == 0.0

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
                "model": "Systran/faster-whisper-large-v3",
                "sample_rate": "16000",
                "enable_vad": "true",
                "interim_results": "true",
                "word_timestamps": "true",
                "vocabulary": json.dumps(vocab),
                "lag_warning_seconds": "2.5",
                "lag_hard_seconds": "6.5",
                "lag_hard_grace_seconds": "1.25",
                "debug_chunk_sleep_initial_seconds": "0.15",
                "debug_chunk_sleep_increment_seconds": "0.05",
            }
        )
        path = f"/session?{params}"
        config = engine._parse_connection_params(path)

        assert config.session_id == "sess_test123"
        assert config.language == "en"
        assert config.model == "Systran/faster-whisper-large-v3"
        assert config.sample_rate == 16000
        assert config.enable_vad is True
        assert config.interim_results is True
        assert config.word_timestamps is True
        assert config.vocabulary == vocab
        assert config.lag_warning_seconds == 2.5
        assert config.lag_hard_seconds == 6.5
        assert config.lag_hard_grace_seconds == 1.25
        assert config.debug_chunk_sleep_initial_seconds == 0.15
        assert config.debug_chunk_sleep_increment_seconds == 0.05

    def test_parse_lag_config_from_env_defaults(self, engine, monkeypatch):
        """Test lag configuration defaults can come from environment."""
        monkeypatch.setenv("DALSTON_REALTIME_LAG_WARNING_SECONDS", "1.8")
        monkeypatch.setenv("DALSTON_REALTIME_LAG_HARD_SECONDS", "4.2")
        monkeypatch.setenv("DALSTON_REALTIME_LAG_HARD_GRACE_SECONDS", "0.9")
        monkeypatch.setenv("DALSTON_REALTIME_DEBUG_CHUNK_SLEEP_INITIAL_SECONDS", "0.2")
        monkeypatch.setenv(
            "DALSTON_REALTIME_DEBUG_CHUNK_SLEEP_INCREMENT_SECONDS", "0.03"
        )

        config = engine._parse_connection_params("/session")

        assert config.lag_warning_seconds == 1.8
        assert config.lag_hard_seconds == 4.2
        assert config.lag_hard_grace_seconds == 0.9
        assert config.debug_chunk_sleep_initial_seconds == 0.2
        assert config.debug_chunk_sleep_increment_seconds == 0.03

    def test_parse_lag_config_invalid_order_raises(self, engine):
        """Hard threshold must be greater than warning threshold."""
        with pytest.raises(ValueError, match="lag_hard_seconds must be greater"):
            engine._parse_connection_params(
                "/session?lag_warning_seconds=5.0&lag_hard_seconds=5.0"
            )

    def test_parse_debug_chunk_sleep_negative_raises(self, engine):
        """Debug sleep values must be non-negative."""
        with pytest.raises(ValueError, match="debug_chunk_sleep_initial_seconds"):
            engine._parse_connection_params(
                "/session?debug_chunk_sleep_initial_seconds=-0.1"
            )


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
