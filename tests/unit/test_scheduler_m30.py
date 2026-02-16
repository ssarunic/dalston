"""Unit tests for M30 scheduler enhancements.

Tests:
- Performance-based timeout calculation
- Error details building
"""

from __future__ import annotations

from dalston.orchestrator.scheduler import (
    DEFAULT_RTF,
    MIN_TIMEOUT_S,
    TIMEOUT_SAFETY_FACTOR,
    calculate_task_timeout,
)


class TestCalculateTaskTimeout:
    """Tests for calculate_task_timeout function."""

    def test_basic_gpu_timeout(self):
        """Should calculate timeout based on GPU RTF."""
        # 1 hour audio, RTF 0.05 (20x faster than real-time)
        # Expected: 3600 * 0.05 * 3 = 540 seconds
        timeout = calculate_task_timeout(
            audio_duration_s=3600,
            rtf_gpu=0.05,
        )
        assert timeout == 540

    def test_basic_cpu_timeout(self):
        """Should calculate timeout based on CPU RTF when GPU not available."""
        # 1 hour audio, RTF 0.8 (1.25x faster than real-time)
        # Expected: 3600 * 0.8 * 3 = 8640 seconds
        timeout = calculate_task_timeout(
            audio_duration_s=3600,
            rtf_gpu=None,
            rtf_cpu=0.8,
        )
        assert timeout == 8640

    def test_prefers_gpu_when_both_available(self):
        """Should prefer GPU RTF when both are available."""
        timeout = calculate_task_timeout(
            audio_duration_s=3600,
            rtf_gpu=0.05,
            rtf_cpu=0.8,
            use_gpu=True,
        )
        # GPU: 3600 * 0.05 * 3 = 540
        assert timeout == 540

    def test_uses_cpu_when_requested(self):
        """Should use CPU RTF when use_gpu=False."""
        timeout = calculate_task_timeout(
            audio_duration_s=3600,
            rtf_gpu=0.05,
            rtf_cpu=0.8,
            use_gpu=False,
        )
        # CPU: 3600 * 0.8 * 3 = 8640
        assert timeout == 8640

    def test_minimum_timeout(self):
        """Should enforce minimum timeout."""
        # Very short audio with fast RTF
        # 10s * 0.01 * 3 = 0.3s, but min is 60s
        timeout = calculate_task_timeout(
            audio_duration_s=10,
            rtf_gpu=0.01,
        )
        assert timeout == MIN_TIMEOUT_S

    def test_default_rtf_when_not_specified(self):
        """Should use default RTF when none specified."""
        timeout = calculate_task_timeout(
            audio_duration_s=3600,
            rtf_gpu=None,
            rtf_cpu=None,
        )
        # 3600 * 1.0 * 3 = 10800
        assert timeout == int(3600 * DEFAULT_RTF * TIMEOUT_SAFETY_FACTOR)

    def test_none_audio_duration_returns_default(self):
        """Should return default timeout when audio duration is None."""
        timeout = calculate_task_timeout(
            audio_duration_s=None,
            rtf_gpu=0.05,
        )
        # Default: MIN_TIMEOUT_S * 5 = 300
        assert timeout == MIN_TIMEOUT_S * 5

    def test_zero_audio_duration_returns_default(self):
        """Should return default timeout when audio duration is zero."""
        timeout = calculate_task_timeout(
            audio_duration_s=0,
            rtf_gpu=0.05,
        )
        assert timeout == MIN_TIMEOUT_S * 5

    def test_negative_audio_duration_returns_default(self):
        """Should return default timeout when audio duration is negative."""
        timeout = calculate_task_timeout(
            audio_duration_s=-100,
            rtf_gpu=0.05,
        )
        assert timeout == MIN_TIMEOUT_S * 5

    def test_returns_integer(self):
        """Should return an integer value."""
        timeout = calculate_task_timeout(
            audio_duration_s=100,
            rtf_gpu=0.033,  # Would give non-integer
        )
        assert isinstance(timeout, int)

    def test_realistic_transcription_scenario(self):
        """Test a realistic transcription scenario."""
        # 30 minute audio file with fast GPU
        # 1800s * 0.05 * 3 = 270s (4.5 minutes)
        timeout = calculate_task_timeout(
            audio_duration_s=1800,
            rtf_gpu=0.05,
            rtf_cpu=0.8,
        )
        assert timeout == 270

    def test_cpu_only_engine(self):
        """Test an engine that only supports CPU."""
        # 10 minute audio with CPU-only engine
        # 600s * 0.5 * 3 = 900s (15 minutes)
        timeout = calculate_task_timeout(
            audio_duration_s=600,
            rtf_gpu=None,
            rtf_cpu=0.5,
        )
        assert timeout == 900

    def test_falls_back_to_gpu_if_cpu_not_available(self):
        """Should fall back to GPU RTF if CPU not specified and use_gpu=False."""
        timeout = calculate_task_timeout(
            audio_duration_s=3600,
            rtf_gpu=0.05,
            rtf_cpu=None,
            use_gpu=False,
        )
        # Falls back to GPU: 3600 * 0.05 * 3 = 540
        assert timeout == 540


class TestErrorDetails:
    """Tests for error details building."""

    def test_engine_info_to_dict(self):
        """EngineInfo should serialize to dict correctly."""
        from dalston.orchestrator.exceptions import EngineInfo

        info = EngineInfo(
            id="faster-whisper",
            languages=["en", "es"],
            supports_word_timestamps=True,
            status="running",
        )

        result = info.to_dict()

        assert result["id"] == "faster-whisper"
        assert result["languages"] == ["en", "es"]
        assert result["word_timestamps"] is True
        assert result["status"] == "running"

    def test_engine_info_with_null_languages(self):
        """EngineInfo with None languages (all) should serialize correctly."""
        from dalston.orchestrator.exceptions import EngineInfo

        info = EngineInfo(
            id="whisper",
            languages=None,
            supports_word_timestamps=True,
            status="available",
        )

        result = info.to_dict()
        assert result["languages"] is None

    def test_error_details_to_dict(self):
        """ErrorDetails should serialize to dict correctly."""
        from dalston.orchestrator.exceptions import EngineInfo, ErrorDetails

        details = ErrorDetails(
            required={"stage": "transcribe", "language": "hr"},
            available_engines=[
                EngineInfo(id="whisper", languages=None, status="available"),
                EngineInfo(id="parakeet", languages=["en"], status="running"),
            ],
            suggestion="Start whisper (supports all languages)",
        )

        result = details.to_dict()

        assert result["required"] == {"stage": "transcribe", "language": "hr"}
        assert len(result["available_engines"]) == 2
        assert result["available_engines"][0]["id"] == "whisper"
        assert result["suggestion"] == "Start whisper (supports all languages)"

    def test_build_engine_suggestion_no_engines(self):
        """Should suggest checking deployment when no engines available."""
        from dalston.orchestrator.exceptions import build_engine_suggestion

        suggestion = build_engine_suggestion(
            stage="transcribe",
            language="en",
            available_engines=[],
        )

        assert "No engines configured" in suggestion
        assert "transcribe" in suggestion

    def test_build_engine_suggestion_with_available_engine(self):
        """Should suggest starting available engines."""
        from dalston.orchestrator.exceptions import EngineInfo, build_engine_suggestion

        engines = [
            EngineInfo(id="whisper", languages=None, status="available"),
            EngineInfo(id="parakeet", languages=["en"], status="running"),
        ]

        suggestion = build_engine_suggestion(
            stage="transcribe",
            language="hr",
            available_engines=engines,
        )

        assert "whisper" in suggestion
        assert "all languages" in suggestion

    def test_build_engine_suggestion_with_language_specific_engine(self):
        """Should suggest language-specific engines when available."""
        from dalston.orchestrator.exceptions import EngineInfo, build_engine_suggestion

        engines = [
            EngineInfo(id="hr-model", languages=["hr"], status="available"),
        ]

        suggestion = build_engine_suggestion(
            stage="transcribe",
            language="hr",
            available_engines=engines,
        )

        assert "hr-model" in suggestion
        assert "hr" in suggestion


class TestExceptionSerialization:
    """Tests for exception to_dict methods."""

    def test_catalog_validation_error_to_dict(self):
        """CatalogValidationError should serialize with details."""
        from dalston.orchestrator.exceptions import (
            CatalogValidationError,
            EngineInfo,
            ErrorDetails,
        )

        details = ErrorDetails(
            required={"stage": "transcribe", "language": "hr"},
            available_engines=[
                EngineInfo(id="whisper", languages=None, status="available"),
            ],
            suggestion="Start whisper",
        )

        error = CatalogValidationError(
            "No engine supports language 'hr'",
            stage="transcribe",
            language="hr",
            details=details,
        )

        result = error.to_dict()

        assert result["error"] == "catalog_validation_error"
        assert "hr" in result["message"]
        assert result["stage"] == "transcribe"
        assert result["language"] == "hr"
        assert "details" in result
        assert result["details"]["suggestion"] == "Start whisper"

    def test_engine_unavailable_error_to_dict(self):
        """EngineUnavailableError should serialize with details."""
        from dalston.orchestrator.exceptions import (
            EngineInfo,
            EngineUnavailableError,
            ErrorDetails,
        )

        details = ErrorDetails(
            required={"stage": "transcribe"},
            available_engines=[
                EngineInfo(id="whisper", languages=None, status="available"),
            ],
        )

        error = EngineUnavailableError(
            "Engine 'whisper' is not running",
            engine_id="whisper",
            stage="transcribe",
            details=details,
        )

        result = error.to_dict()

        assert result["error"] == "engine_unavailable"
        assert result["engine_id"] == "whisper"
        assert result["stage"] == "transcribe"
        assert "details" in result

    def test_engine_capability_error_to_dict(self):
        """EngineCapabilityError should serialize with details."""
        from dalston.orchestrator.exceptions import (
            EngineCapabilityError,
            EngineInfo,
            ErrorDetails,
        )

        details = ErrorDetails(
            required={"stage": "transcribe", "language": "hr"},
            available_engines=[
                EngineInfo(id="parakeet", languages=["en"], status="running"),
            ],
        )

        error = EngineCapabilityError(
            "Engine does not support language 'hr'",
            engine_id="parakeet",
            stage="transcribe",
            language="hr",
            details=details,
        )

        result = error.to_dict()

        assert result["error"] == "engine_capability_mismatch"
        assert result["engine_id"] == "parakeet"
        assert result["language"] == "hr"
        assert "details" in result
