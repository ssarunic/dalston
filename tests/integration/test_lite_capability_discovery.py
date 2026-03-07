"""Integration tests for the lite capability discovery endpoint (M58 Phase 4).

GET /v1/lite/capabilities
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from dalston.gateway.api.v1.engines import lite_router
from dalston.orchestrator.lite_capabilities import (
    CAPABILITY_MATRIX,
    DEFAULT_PROFILE,
    MATRIX_VERSION,
    LiteProfile,
    get_matrix_as_dict,
)


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(lite_router, prefix="/v1")
    return app


class TestLiteCapabilitiesEndpoint:
    def setup_method(self) -> None:
        self.app = _make_app()
        self.client = TestClient(self.app)

    def test_endpoint_returns_200(self) -> None:
        response = self.client.get("/v1/lite/capabilities")
        assert response.status_code == 200

    def test_response_is_json(self) -> None:
        response = self.client.get("/v1/lite/capabilities")
        data = response.json()
        assert isinstance(data, dict)

    def test_response_has_schema_version(self) -> None:
        response = self.client.get("/v1/lite/capabilities")
        data = response.json()
        assert data["schema_version"] == MATRIX_VERSION

    def test_response_has_default_profile(self) -> None:
        response = self.client.get("/v1/lite/capabilities")
        data = response.json()
        assert data["default_profile"] == DEFAULT_PROFILE

    def test_response_has_all_profiles(self) -> None:
        response = self.client.get("/v1/lite/capabilities")
        data = response.json()
        profiles = data["profiles"]
        for profile in LiteProfile:
            assert profile.value in profiles, (
                f"Profile '{profile.value}' missing from /v1/lite/capabilities response"
            )

    def test_core_profile_has_correct_stages(self) -> None:
        response = self.client.get("/v1/lite/capabilities")
        data = response.json()
        core = data["profiles"]["core"]
        assert core["stages"] == ["prepare", "transcribe", "merge"]

    def test_speaker_profile_has_diarize_stage(self) -> None:
        response = self.client.get("/v1/lite/capabilities")
        data = response.json()
        speaker = data["profiles"]["speaker"]
        assert "diarize" in speaker["stages"]

    def test_compliance_profile_has_pii_detect_stage(self) -> None:
        response = self.client.get("/v1/lite/capabilities")
        data = response.json()
        compliance = data["profiles"]["compliance"]
        assert "pii_detect" in compliance["stages"]

    def test_response_has_active_profile_field(self) -> None:
        response = self.client.get("/v1/lite/capabilities")
        data = response.json()
        assert "active_profile" in data
        # Default is "core" when DALSTON_LITE_PROFILE is not set
        assert data["active_profile"] == DEFAULT_PROFILE

    def test_active_profile_reflects_env_var(self, monkeypatch) -> None:
        monkeypatch.setenv("DALSTON_LITE_PROFILE", "speaker")
        response = self.client.get("/v1/lite/capabilities")
        data = response.json()
        assert data["active_profile"] == "speaker"

    def test_response_has_missing_prereqs_field(self) -> None:
        response = self.client.get("/v1/lite/capabilities")
        data = response.json()
        assert "missing_prereqs" in data
        assert isinstance(data["missing_prereqs"], dict)

    def test_compliance_prereqs_listed_when_absent(self) -> None:
        """compliance profile shows missing packages when presidio not installed."""
        from unittest.mock import patch

        absent = ["presidio_analyzer", "presidio_anonymizer"]

        def _fake_check(profile: LiteProfile) -> list[str]:
            return absent if profile == LiteProfile.COMPLIANCE else []

        with patch(
            "dalston.gateway.api.v1.engines.check_prerequisites",
            side_effect=_fake_check,
        ):
            response = self.client.get("/v1/lite/capabilities")

        data = response.json()
        assert "compliance" in data["missing_prereqs"]
        assert set(data["missing_prereqs"]["compliance"]) == set(absent)

    def test_profile_descriptions_are_non_empty(self) -> None:
        response = self.client.get("/v1/lite/capabilities")
        data = response.json()
        for name, profile_data in data["profiles"].items():
            assert isinstance(profile_data["description"], str)
            assert len(profile_data["description"]) > 0, (
                f"Profile '{name}' has empty description"
            )

    def test_response_derived_from_canonical_source(self) -> None:
        """Verify the endpoint response matches get_matrix_as_dict() exactly."""
        matrix = get_matrix_as_dict()
        response = self.client.get("/v1/lite/capabilities")
        data = response.json()

        assert data["schema_version"] == matrix["schema_version"]
        assert data["default_profile"] == matrix["default_profile"]

        for profile_name, cap in matrix["profiles"].items():
            assert profile_name in data["profiles"]
            resp_profile = data["profiles"][profile_name]
            assert resp_profile["stages"] == cap["stages"]
            assert resp_profile["supported_options"] == cap["supported_options"]

    def test_profile_precedence_field_present(self) -> None:
        response = self.client.get("/v1/lite/capabilities")
        data = response.json()
        assert "profile_precedence" in data
        assert isinstance(data["profile_precedence"], list)
        assert len(data["profile_precedence"]) > 0

    def test_core_speaker_detection_option_is_false(self) -> None:
        response = self.client.get("/v1/lite/capabilities")
        data = response.json()
        core_opts = data["profiles"]["core"]["supported_options"]
        assert core_opts["speaker_detection"] is False

    def test_speaker_speaker_detection_option_is_true(self) -> None:
        response = self.client.get("/v1/lite/capabilities")
        data = response.json()
        speaker_opts = data["profiles"]["speaker"]["supported_options"]
        assert speaker_opts["speaker_detection"] is True

    def test_compliance_pii_detection_option_is_true(self) -> None:
        response = self.client.get("/v1/lite/capabilities")
        data = response.json()
        compliance_opts = data["profiles"]["compliance"]["supported_options"]
        assert compliance_opts["pii_detection"] is True

    def test_endpoint_available_in_any_runtime_mode(self, monkeypatch) -> None:
        """GET /v1/lite/capabilities must work in both distributed and lite mode."""
        for mode in ("distributed", "lite"):
            monkeypatch.setenv("DALSTON_MODE", mode)
            response = self.client.get("/v1/lite/capabilities")
            assert response.status_code == 200, (
                f"Endpoint returned {response.status_code} in {mode} mode"
            )


class TestLiteCapabilitiesMatrix:
    """Verify that the matrix returned is the canonical one (not a copy)."""

    def test_matrix_completeness(self) -> None:
        matrix = get_matrix_as_dict()
        assert set(matrix["profiles"].keys()) == {p.value for p in LiteProfile}

    def test_matrix_version_stable(self) -> None:
        """Matrix version is pinned — update this test when MATRIX_VERSION changes."""
        assert MATRIX_VERSION == "1.0.0"

    def test_compliance_missing_prereqs_deterministic(self) -> None:
        """compliance prereqs list must always include presidio packages."""
        compliance = CAPABILITY_MATRIX[LiteProfile.COMPLIANCE]
        prereqs = compliance.requires_prereqs
        assert "presidio_analyzer" in prereqs
        assert "presidio_anonymizer" in prereqs
