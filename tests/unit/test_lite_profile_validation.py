"""Unit tests for lite capability matrix and profile validation (M58 Phase 1)."""

from __future__ import annotations

import pytest

from dalston.orchestrator.lite_capabilities import (
    CAPABILITY_MATRIX,
    DEFAULT_PROFILE,
    MATRIX_VERSION,
    LitePrerequisiteMissingError,
    LiteProfile,
    LiteProfileNotFoundError,
    LiteUnsupportedFeatureError,
    ProfileCapability,
    get_active_profile,
    get_active_profile_name,
    get_matrix_as_dict,
    resolve_profile,
    validate_request,
)


class TestResolveProfile:
    def test_resolve_core_by_name(self) -> None:
        cap = resolve_profile("core")
        assert cap.profile == LiteProfile.CORE

    def test_resolve_speaker_by_name(self) -> None:
        cap = resolve_profile("speaker")
        assert cap.profile == LiteProfile.SPEAKER

    def test_resolve_compliance_by_name(self) -> None:
        cap = resolve_profile("compliance")
        assert cap.profile == LiteProfile.COMPLIANCE

    def test_resolve_case_insensitive(self) -> None:
        assert resolve_profile("CORE").profile == LiteProfile.CORE
        assert resolve_profile("Speaker").profile == LiteProfile.SPEAKER

    def test_resolve_trims_whitespace(self) -> None:
        assert resolve_profile("  core  ").profile == LiteProfile.CORE

    def test_unknown_profile_raises(self) -> None:
        with pytest.raises(LiteProfileNotFoundError) as exc_info:
            resolve_profile("unknown_profile")
        assert "unknown_profile" in str(exc_info.value)
        assert exc_info.value.profile_name == "unknown_profile"

    def test_unknown_profile_to_dict(self) -> None:
        try:
            resolve_profile("bogus")
        except LiteProfileNotFoundError as exc:
            d = exc.to_dict()
            assert d["error"] == "lite_profile_not_found"
            assert d["profile"] == "bogus"
            assert "core" in d["valid_profiles"]


class TestGetActiveProfile:
    def test_default_is_core(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DALSTON_LITE_PROFILE", raising=False)
        assert get_active_profile_name() == DEFAULT_PROFILE
        cap = get_active_profile()
        assert cap.profile == LiteProfile.CORE

    def test_env_var_overrides_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DALSTON_LITE_PROFILE", "speaker")
        assert get_active_profile_name() == "speaker"
        cap = get_active_profile()
        assert cap.profile == LiteProfile.SPEAKER

    def test_env_var_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DALSTON_LITE_PROFILE", "SPEAKER")
        cap = get_active_profile()
        assert cap.profile == LiteProfile.SPEAKER


class TestValidateRequest:
    # ------------------------------------------------------------------
    # core profile guardrails
    # ------------------------------------------------------------------

    def test_core_allows_language(self) -> None:
        # Should not raise — language is supported in core.
        validate_request(LiteProfile.CORE, {"language": "en"})

    def test_core_allows_timestamps(self) -> None:
        validate_request(LiteProfile.CORE, {"timestamps_granularity": "word"})

    def test_core_allows_none_speaker_detection(self) -> None:
        validate_request(LiteProfile.CORE, {"speaker_detection": "none"})

    def test_core_allows_empty_parameters(self) -> None:
        validate_request(LiteProfile.CORE, {})

    def test_core_rejects_diarize_speaker_detection(self) -> None:
        with pytest.raises(LiteUnsupportedFeatureError) as exc_info:
            validate_request(LiteProfile.CORE, {"speaker_detection": "diarize"})
        exc = exc_info.value
        assert exc.feature == "speaker_detection"
        assert exc.profile == LiteProfile.CORE
        assert "speaker" in exc.remediation.lower()

    def test_core_rejects_per_channel_speaker_detection(self) -> None:
        # core doesn't support speaker_detection at all; per_channel is caught
        # by the broader "speaker_detection not supported" guard.
        with pytest.raises(LiteUnsupportedFeatureError) as exc_info:
            validate_request(LiteProfile.CORE, {"speaker_detection": "per_channel"})
        assert exc_info.value.feature == "speaker_detection"

    def test_core_rejects_pii_detection(self) -> None:
        with pytest.raises(LiteUnsupportedFeatureError) as exc_info:
            validate_request(LiteProfile.CORE, {"pii_detection": True})
        exc = exc_info.value
        assert exc.feature == "pii_detection"
        assert exc.profile == LiteProfile.CORE
        assert "compliance" in exc.remediation.lower()

    def test_core_rejects_redact_pii_audio(self) -> None:
        with pytest.raises(LiteUnsupportedFeatureError) as exc_info:
            validate_request(LiteProfile.CORE, {"redact_pii_audio": True})
        assert exc_info.value.feature == "redact_pii_audio"

    # ------------------------------------------------------------------
    # speaker profile guardrails
    # ------------------------------------------------------------------

    def test_speaker_allows_diarize(self) -> None:
        validate_request(LiteProfile.SPEAKER, {"speaker_detection": "diarize"})

    def test_speaker_allows_num_speakers(self) -> None:
        validate_request(
            LiteProfile.SPEAKER,
            {"speaker_detection": "diarize", "num_speakers": 2},
        )

    def test_speaker_rejects_per_channel(self) -> None:
        with pytest.raises(LiteUnsupportedFeatureError) as exc_info:
            validate_request(LiteProfile.SPEAKER, {"speaker_detection": "per_channel"})
        assert exc_info.value.feature == "speaker_detection"
        assert "per_channel" in exc_info.value.remediation

    def test_speaker_rejects_pii_detection(self) -> None:
        with pytest.raises(LiteUnsupportedFeatureError) as exc_info:
            validate_request(LiteProfile.SPEAKER, {"pii_detection": True})
        assert exc_info.value.feature == "pii_detection"

    # ------------------------------------------------------------------
    # compliance profile guardrails
    # ------------------------------------------------------------------

    def test_compliance_allows_pii_detection(self) -> None:
        validate_request(LiteProfile.COMPLIANCE, {"pii_detection": True})

    def test_compliance_allows_redact_pii_audio(self) -> None:
        validate_request(
            LiteProfile.COMPLIANCE,
            {"pii_detection": True, "redact_pii_audio": True},
        )

    def test_compliance_rejects_speaker_detection(self) -> None:
        with pytest.raises(LiteUnsupportedFeatureError) as exc_info:
            validate_request(LiteProfile.COMPLIANCE, {"speaker_detection": "diarize"})
        assert exc_info.value.feature == "speaker_detection"

    # ------------------------------------------------------------------
    # Error structure
    # ------------------------------------------------------------------

    def test_unsupported_error_to_dict(self) -> None:
        try:
            validate_request(LiteProfile.CORE, {"speaker_detection": "diarize"})
        except LiteUnsupportedFeatureError as exc:
            d = exc.to_dict()
            assert d["error"] == "lite_unsupported_feature"
            assert d["feature"] == "speaker_detection"
            assert d["profile"] == "core"
            assert isinstance(d["remediation"], str)
            assert len(d["remediation"]) > 0

    def test_unsupported_error_upgrade_profiles(self) -> None:
        """Errors should suggest profiles that do support the feature."""
        try:
            validate_request(LiteProfile.CORE, {"speaker_detection": "diarize"})
        except LiteUnsupportedFeatureError as exc:
            d = exc.to_dict()
            # speaker profile supports speaker_detection
            assert "speaker" in d["upgrade_profiles"]


class TestCapabilityMatrix:
    def test_all_profiles_present(self) -> None:
        for profile in LiteProfile:
            assert profile in CAPABILITY_MATRIX

    def test_all_capabilities_have_version(self) -> None:
        for cap in CAPABILITY_MATRIX.values():
            assert cap.version == MATRIX_VERSION

    def test_core_has_expected_stages(self) -> None:
        core = CAPABILITY_MATRIX[LiteProfile.CORE]
        assert core.stages == ["prepare", "transcribe", "merge"]

    def test_speaker_has_diarize_stage(self) -> None:
        speaker = CAPABILITY_MATRIX[LiteProfile.SPEAKER]
        assert "diarize" in speaker.stages

    def test_compliance_has_pii_detect_stage(self) -> None:
        compliance = CAPABILITY_MATRIX[LiteProfile.COMPLIANCE]
        assert "pii_detect" in compliance.stages

    def test_core_no_prereqs(self) -> None:
        assert CAPABILITY_MATRIX[LiteProfile.CORE].requires_prereqs == []

    def test_speaker_no_prereqs(self) -> None:
        assert CAPABILITY_MATRIX[LiteProfile.SPEAKER].requires_prereqs == []

    def test_compliance_has_prereqs(self) -> None:
        prereqs = CAPABILITY_MATRIX[LiteProfile.COMPLIANCE].requires_prereqs
        assert len(prereqs) > 0
        # Should mention presidio
        combined = " ".join(prereqs)
        assert "presidio" in combined

    def test_core_is_m56_m57_baseline(self) -> None:
        """M56/M57 zero-config path must remain unchanged in core profile."""
        core = CAPABILITY_MATRIX[LiteProfile.CORE]
        assert core.stages == ["prepare", "transcribe", "merge"]
        assert core.supported_options["speaker_detection"] is False
        assert core.supported_options["pii_detection"] is False

    def test_get_matrix_as_dict_structure(self) -> None:
        d = get_matrix_as_dict()
        assert d["schema_version"] == MATRIX_VERSION
        assert d["default_profile"] == DEFAULT_PROFILE
        assert "profiles" in d
        for profile in LiteProfile:
            assert profile.value in d["profiles"]

    def test_matrix_profiles_are_pydantic_models(self) -> None:
        for cap in CAPABILITY_MATRIX.values():
            assert isinstance(cap, ProfileCapability)


class TestLitePrerequisiteMissingError:
    def test_error_message_includes_packages(self) -> None:
        exc = LitePrerequisiteMissingError(
            LiteProfile.COMPLIANCE,
            ["presidio_analyzer", "presidio_anonymizer"],
        )
        msg = str(exc)
        assert "presidio_analyzer" in msg
        assert "presidio_anonymizer" in msg

    def test_to_dict_structure(self) -> None:
        exc = LitePrerequisiteMissingError(
            LiteProfile.COMPLIANCE,
            ["presidio_analyzer"],
        )
        d = exc.to_dict()
        assert d["error"] == "lite_prerequisite_missing"
        assert d["profile"] == "compliance"
        assert "presidio_analyzer" in d["missing_packages"]
        assert "pip install" in d["remediation"]
