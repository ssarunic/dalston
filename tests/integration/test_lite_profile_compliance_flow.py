"""Integration tests for the lite 'compliance' profile (M58 Phase 2).

The compliance profile is conditional: it requires ``presidio_analyzer`` and
``presidio_anonymizer`` to be installed.  These packages are typically absent
in CI, so the tests are structured to cover both scenarios:

1. Prerequisites absent → ``LitePrerequisiteMissingError`` on pipeline
   construction; deterministic, actionable error message.
2. Prerequisites present (mocked) → pipeline runs prepare → transcribe →
   pii_detect → merge and produces a transcript with ``pii_entities``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from dalston.config import get_settings
from dalston.orchestrator.lite_capabilities import (
    LitePrerequisiteMissingError,
    LiteProfile,
    LiteUnsupportedFeatureError,
    check_prerequisites,
    resolve_profile,
    validate_request,
)


@pytest.fixture(autouse=True)
def _lite_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DALSTON_MODE", "lite")
    monkeypatch.setenv("DALSTON_LITE_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestCompliancePrerequisites:
    def test_compliance_has_prereqs_in_matrix(self) -> None:
        cap = resolve_profile("compliance")
        assert len(cap.requires_prereqs) > 0

    def test_check_prerequisites_returns_missing_list(self) -> None:
        """check_prerequisites returns a list of missing packages."""
        missing = check_prerequisites(LiteProfile.COMPLIANCE)
        # Either empty (prereqs installed) or a list of strings
        assert isinstance(missing, list)
        for item in missing:
            assert isinstance(item, str)

    def test_compliance_fails_deterministically_when_prereqs_absent(
        self,
    ) -> None:
        """If presidio packages are not installed, LitePrerequisiteMissingError
        must be raised with a clear remediation hint (not a generic ImportError)."""
        # Temporarily hide presidio packages so check_prerequisites reports them missing.
        real_modules = {}
        for pkg in ("presidio_analyzer", "presidio_anonymizer"):
            real_modules[pkg] = sys.modules.pop(pkg, None)

        try:
            with patch(
                "dalston.orchestrator.lite_main.check_prerequisites",
                return_value=["presidio_analyzer", "presidio_anonymizer"],
            ):
                from dalston.orchestrator.lite_main import build_pipeline

                with pytest.raises(LitePrerequisiteMissingError) as exc_info:
                    build_pipeline("compliance")

            exc = exc_info.value
            assert exc.profile == LiteProfile.COMPLIANCE
            assert len(exc.missing) > 0
            # Error message must include remediation
            assert "pip install" in str(exc)
        finally:
            # Restore any modules we popped
            for pkg, mod in real_modules.items():
                if mod is not None:
                    sys.modules[pkg] = mod

    def test_compliance_error_to_dict_is_actionable(self) -> None:
        exc = LitePrerequisiteMissingError(
            LiteProfile.COMPLIANCE,
            ["presidio_analyzer", "presidio_anonymizer"],
        )
        d = exc.to_dict()
        assert d["error"] == "lite_prerequisite_missing"
        assert d["profile"] == "compliance"
        assert "presidio_analyzer" in d["missing_packages"]
        assert "pip install" in d["remediation"]


class TestComplianceValidation:
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
        assert exc_info.value.profile == LiteProfile.COMPLIANCE

    def test_core_rejects_pii_detection_with_compliance_hint(self) -> None:
        """Error for pii_detection in core must hint at compliance profile."""
        with pytest.raises(LiteUnsupportedFeatureError) as exc_info:
            validate_request(LiteProfile.CORE, {"pii_detection": True})
        assert "compliance" in exc_info.value.remediation.lower()


class TestComplianceEndToEnd:
    """Run compliance pipeline with prerequisites mocked as available."""

    @pytest.mark.asyncio
    async def test_compliance_pipeline_completes_with_prereqs_mocked(
        self, tmp_path: Path
    ) -> None:
        with patch(
            "dalston.orchestrator.lite_main.check_prerequisites",
            return_value=[],
        ):
            from dalston.orchestrator.lite_main import build_pipeline

            pipeline = build_pipeline("compliance")
            result = await pipeline.run_job(
                b"audio-bytes",
                parameters={"pii_detection": True},
            )

        assert "job_id" in result
        assert result["transcript_uri"].startswith("file://")

    @pytest.mark.asyncio
    async def test_compliance_transcript_has_pii_entities_field(
        self, tmp_path: Path
    ) -> None:
        with patch(
            "dalston.orchestrator.lite_main.check_prerequisites",
            return_value=[],
        ):
            from dalston.orchestrator.lite_main import build_pipeline

            pipeline = build_pipeline("compliance")
            result = await pipeline.run_job(
                b"audio-bytes",
                parameters={"pii_detection": True},
            )

        transcript_path = Path(result["transcript_uri"].removeprefix("file://"))
        data = json.loads(transcript_path.read_text())
        assert data["status"] == "completed"
        assert data["profile"] == LiteProfile.COMPLIANCE.value
        assert "pii_entities" in data
        assert isinstance(data["pii_entities"], list)

    @pytest.mark.asyncio
    async def test_compliance_pii_detect_artifact_written(self, tmp_path: Path) -> None:
        with patch(
            "dalston.orchestrator.lite_main.check_prerequisites",
            return_value=[],
        ):
            from dalston.orchestrator.lite_main import build_pipeline

            pipeline = build_pipeline("compliance")
            result = await pipeline.run_job(
                b"audio-bytes",
                parameters={"pii_detection": True},
            )

        job_id = result["job_id"]
        artifacts_root = tmp_path / "artifacts"
        pii_output = (
            artifacts_root / "jobs" / job_id / "tasks" / "pii_detect" / "output.json"
        )
        assert pii_output.exists(), "pii_detect stage must write output.json"
        data = json.loads(pii_output.read_text())
        assert "entities" in data

    @pytest.mark.asyncio
    async def test_compliance_pipeline_rejects_speaker_detection(
        self, tmp_path: Path
    ) -> None:
        with patch(
            "dalston.orchestrator.lite_main.check_prerequisites",
            return_value=[],
        ):
            from dalston.orchestrator.lite_main import build_pipeline

            pipeline = build_pipeline("compliance")
            with pytest.raises(LiteUnsupportedFeatureError):
                await pipeline.run_job(
                    b"audio-bytes",
                    parameters={
                        "pii_detection": True,
                        "speaker_detection": "diarize",
                    },
                )
