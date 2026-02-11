"""Unit tests for realtime_sdk VAD module."""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from dalston.realtime_sdk.vad import VADConfig, VADProcessor, VADResult, VADState


class TestVADConfig:
    """Tests for VADConfig dataclass."""

    def test_default_values(self):
        config = VADConfig()

        assert config.speech_threshold == 0.5
        assert config.min_speech_duration == 0.25
        assert config.min_silence_duration == 0.5
        assert config.sample_rate == 16000
        assert config.lookback_chunks == 3

    def test_custom_values(self):
        config = VADConfig(
            speech_threshold=0.3,
            min_speech_duration=0.1,
            min_silence_duration=0.8,
            sample_rate=8000,
            lookback_chunks=5,
        )

        assert config.speech_threshold == 0.3
        assert config.min_speech_duration == 0.1
        assert config.min_silence_duration == 0.8
        assert config.sample_rate == 8000
        assert config.lookback_chunks == 5


class TestVADState:
    """Tests for VADState enum."""

    def test_states(self):
        assert VADState.SILENCE.value == "silence"
        assert VADState.SPEECH.value == "speech"


class TestVADResult:
    """Tests for VADResult dataclass."""

    def test_no_event(self):
        result = VADResult(event=None)

        assert result.event is None
        assert result.speech_audio is None

    def test_speech_start_event(self):
        result = VADResult(event="speech_start")

        assert result.event == "speech_start"
        assert result.speech_audio is None

    def test_speech_end_event_with_audio(self):
        audio = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        result = VADResult(event="speech_end", speech_audio=audio)

        assert result.event == "speech_end"
        assert result.speech_audio is not None
        np.testing.assert_array_equal(result.speech_audio, audio)


class TestVADProcessor:
    """Tests for VADProcessor class."""

    @pytest.fixture
    def mock_silero_model(self):
        """Create a mock Silero VAD model."""
        mock_model = MagicMock()
        mock_model.reset_states = MagicMock()
        return mock_model

    @pytest.fixture
    def vad_processor(self, mock_silero_model):
        """Create a VADProcessor with mocked model."""
        processor = VADProcessor()
        processor._model = mock_silero_model
        return processor

    def test_initial_state(self):
        processor = VADProcessor()

        assert processor.state == VADState.SILENCE
        assert processor.is_speaking is False

    def test_config_override(self):
        config = VADConfig(speech_threshold=0.7)
        processor = VADProcessor(config=config)

        assert processor.config.speech_threshold == 0.7

    @patch("dalston.realtime_sdk.vad.torch")
    def test_silence_to_speech_transition(
        self, mock_torch, vad_processor, mock_silero_model
    ):
        """Test transition from silence to speech state."""
        # Mock high speech probability
        mock_silero_model.return_value.item.return_value = 0.9
        mock_torch.from_numpy.return_value = MagicMock()
        mock_torch.no_grad.return_value.__enter__ = MagicMock()
        mock_torch.no_grad.return_value.__exit__ = MagicMock()

        # Create audio chunk (100ms at 16kHz = 1600 samples)
        audio = np.zeros(1600, dtype=np.float32)

        result = vad_processor.process_chunk(audio)

        assert result.event == "speech_start"
        assert vad_processor.state == VADState.SPEECH
        assert vad_processor.is_speaking is True

    @patch("dalston.realtime_sdk.vad.torch")
    def test_speech_continues(self, mock_torch, vad_processor, mock_silero_model):
        """Test speech continues without event."""
        # Start in speech state
        vad_processor._state = VADState.SPEECH
        vad_processor._speech_duration = 0.5

        # Mock high speech probability
        mock_silero_model.return_value.item.return_value = 0.9
        mock_torch.from_numpy.return_value = MagicMock()
        mock_torch.no_grad.return_value.__enter__ = MagicMock()
        mock_torch.no_grad.return_value.__exit__ = MagicMock()

        audio = np.zeros(1600, dtype=np.float32)
        result = vad_processor.process_chunk(audio)

        assert result.event is None
        assert vad_processor.state == VADState.SPEECH

    @patch("dalston.realtime_sdk.vad.torch")
    def test_speech_to_silence_transition(
        self, mock_torch, vad_processor, mock_silero_model
    ):
        """Test transition from speech to silence with endpoint detection."""
        config = VADConfig(min_silence_duration=0.2, min_speech_duration=0.1)
        vad_processor.config = config

        # Start in speech state with accumulated audio
        vad_processor._state = VADState.SPEECH
        vad_processor._speech_duration = 0.5  # Enough speech duration
        vad_processor._speech_buffer = [np.zeros(1600, dtype=np.float32)]

        # Mock low speech probability (silence)
        mock_silero_model.return_value.item.return_value = 0.1
        mock_torch.from_numpy.return_value = MagicMock()
        mock_torch.no_grad.return_value.__enter__ = MagicMock()
        mock_torch.no_grad.return_value.__exit__ = MagicMock()

        audio = np.zeros(1600, dtype=np.float32)

        # First chunk of silence - not enough for endpoint
        vad_processor._silence_duration = 0.0
        vad_processor.process_chunk(audio)

        # Second chunk - still accumulating silence
        vad_processor._silence_duration = 0.15
        vad_processor.process_chunk(audio)

        # Third chunk - should trigger endpoint
        vad_processor._silence_duration = 0.25
        result3 = vad_processor.process_chunk(audio)

        # The third should trigger speech_end
        assert result3.event == "speech_end"
        assert vad_processor.state == VADState.SILENCE

    def test_flush_during_speech(self, vad_processor):
        """Test flushing remaining audio during speech."""
        vad_processor._state = VADState.SPEECH
        vad_processor._speech_duration = 0.5
        vad_processor._speech_buffer = [
            np.ones(1600, dtype=np.float32),
            np.ones(1600, dtype=np.float32),
        ]

        result = vad_processor.flush()

        assert result is not None
        assert len(result) == 3200  # 2 chunks concatenated

    def test_flush_during_silence(self, vad_processor):
        """Test flushing returns None during silence."""
        vad_processor._state = VADState.SILENCE

        result = vad_processor.flush()

        assert result is None

    def test_flush_short_speech_discarded(self, vad_processor):
        """Test that short speech is discarded on flush."""
        vad_processor._state = VADState.SPEECH
        vad_processor._speech_duration = 0.1  # Less than min_speech_duration
        vad_processor._speech_buffer = [np.ones(1600, dtype=np.float32)]

        result = vad_processor.flush()

        assert result is None

    def test_reset(self, vad_processor, mock_silero_model):
        """Test reset clears all state."""
        vad_processor._state = VADState.SPEECH
        vad_processor._speech_buffer = [np.ones(100, dtype=np.float32)]
        vad_processor._lookback_buffer = [np.ones(100, dtype=np.float32)]
        vad_processor._silence_duration = 0.3
        vad_processor._speech_duration = 0.5

        vad_processor.reset()

        assert vad_processor.state == VADState.SILENCE
        assert len(vad_processor._speech_buffer) == 0
        assert len(vad_processor._lookback_buffer) == 0
        assert vad_processor._silence_duration == 0.0
        assert vad_processor._speech_duration == 0.0
        mock_silero_model.reset_states.assert_called_once()

    def test_lookback_buffer_maintained(self, vad_processor, mock_silero_model):
        """Test lookback buffer is maintained at correct size."""
        with patch("dalston.realtime_sdk.vad.torch") as mock_torch:
            mock_silero_model.return_value.item.return_value = 0.1  # Silence
            mock_torch.from_numpy.return_value = MagicMock()
            mock_torch.no_grad.return_value.__enter__ = MagicMock()
            mock_torch.no_grad.return_value.__exit__ = MagicMock()

            # Process more chunks than lookback_chunks
            for _ in range(5):
                audio = np.zeros(1600, dtype=np.float32)
                vad_processor.process_chunk(audio)

            # Lookback buffer should be capped at lookback_chunks (default 3)
            assert len(vad_processor._lookback_buffer) == 3

    def test_is_speaking_property(self, vad_processor):
        """Test is_speaking property reflects state."""
        assert vad_processor.is_speaking is False

        vad_processor._state = VADState.SPEECH
        assert vad_processor.is_speaking is True

        vad_processor._state = VADState.SILENCE
        assert vad_processor.is_speaking is False

    def test_state_property(self, vad_processor):
        """Test state property returns current state."""
        assert vad_processor.state == VADState.SILENCE

        vad_processor._state = VADState.SPEECH
        assert vad_processor.state == VADState.SPEECH
