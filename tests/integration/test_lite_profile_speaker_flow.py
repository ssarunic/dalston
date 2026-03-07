"""Integration tests for the lite 'speaker' profile end-to-end flow (M58 Phase 2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dalston.config import get_settings
from dalston.orchestrator.lite_capabilities import (
    LiteProfile,
    LiteUnsupportedFeatureError,
    validate_request,
)
from dalston.orchestrator.lite_main import build_pipeline


@pytest.fixture(autouse=True)
def _lite_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DALSTON_MODE", "lite")
    monkeypatch.setenv("DALSTON_LITE_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestSpeakerProfileEndToEnd:
    @pytest.mark.asyncio
    async def test_speaker_pipeline_completes(self, tmp_path: Path) -> None:
        pipeline = build_pipeline("speaker")
        result = await pipeline.run_job(
            b"audio-bytes",
            parameters={"speaker_detection": "diarize"},
        )

        assert "job_id" in result
        assert "transcript_uri" in result
        assert result["transcript_uri"].startswith("file://")

    @pytest.mark.asyncio
    async def test_speaker_pipeline_transcript_has_speakers(
        self, tmp_path: Path
    ) -> None:
        pipeline = build_pipeline("speaker")
        result = await pipeline.run_job(
            b"audio-bytes",
            parameters={"speaker_detection": "diarize"},
        )

        transcript_path = Path(result["transcript_uri"].removeprefix("file://"))
        data = json.loads(transcript_path.read_text())

        assert data["status"] == "completed"
        assert data["profile"] == LiteProfile.SPEAKER.value
        assert "speakers" in data
        assert len(data["speakers"]) >= 1
        # Segments must carry speaker labels
        assert len(data["segments"]) >= 1
        for seg in data["segments"]:
            assert "speaker" in seg

    @pytest.mark.asyncio
    async def test_speaker_pipeline_with_num_speakers_hint(
        self, tmp_path: Path
    ) -> None:
        pipeline = build_pipeline("speaker")
        result = await pipeline.run_job(
            b"audio-bytes",
            parameters={"speaker_detection": "diarize", "num_speakers": 3},
        )

        transcript_path = Path(result["transcript_uri"].removeprefix("file://"))
        data = json.loads(transcript_path.read_text())
        # num_speakers=3 hint should surface 3 speaker labels
        assert len(data["speakers"]) == 3

    @pytest.mark.asyncio
    async def test_speaker_pipeline_default_speaker_count(self, tmp_path: Path) -> None:
        pipeline = build_pipeline("speaker")
        result = await pipeline.run_job(b"audio-bytes")

        transcript_path = Path(result["transcript_uri"].removeprefix("file://"))
        data = json.loads(transcript_path.read_text())
        # Default is 2 speakers
        assert len(data["speakers"]) == 2

    @pytest.mark.asyncio
    async def test_speaker_profile_rejects_per_channel(self) -> None:
        """per_channel detection is not supported in any lite profile."""
        with pytest.raises(LiteUnsupportedFeatureError) as exc_info:
            validate_request(LiteProfile.SPEAKER, {"speaker_detection": "per_channel"})
        assert "per_channel" in exc_info.value.feature

    @pytest.mark.asyncio
    async def test_speaker_pipeline_transcript_uri_readable(
        self, tmp_path: Path
    ) -> None:
        pipeline = build_pipeline("speaker")
        result = await pipeline.run_job(
            b"audio-bytes",
            parameters={"speaker_detection": "diarize"},
        )

        transcript_path = Path(result["transcript_uri"].removeprefix("file://"))
        assert transcript_path.exists()
        data = json.loads(transcript_path.read_text())
        assert isinstance(data, dict)

    @pytest.mark.asyncio
    async def test_speaker_profile_diarize_artifacts_written(
        self, tmp_path: Path
    ) -> None:
        pipeline = build_pipeline("speaker")
        result = await pipeline.run_job(
            b"audio-bytes",
            parameters={"speaker_detection": "diarize"},
        )

        job_id = result["job_id"]
        artifacts_root = tmp_path / "artifacts"
        diarize_output = (
            artifacts_root / "jobs" / job_id / "tasks" / "diarize" / "output.json"
        )
        assert diarize_output.exists(), "Diarize stage must write output.json"
        data = json.loads(diarize_output.read_text())
        assert "segments" in data
        assert "speakers" in data


class TestCoreProfileRegressionWithSpeakerModule:
    """Ensure M56/M57 zero-config default (core profile) still works
    even after speaker profile was added to the module."""

    @pytest.mark.asyncio
    async def test_core_pipeline_still_works(self, tmp_path: Path) -> None:
        from dalston.orchestrator.lite_main import build_default_pipeline

        pipeline = build_default_pipeline()
        result = await pipeline.run_job(b"audio-bytes")

        transcript_path = Path(result["transcript_uri"].removeprefix("file://"))
        data = json.loads(transcript_path.read_text())
        assert data["status"] == "completed"
        assert data["text"] == "lite transcript"

    @pytest.mark.asyncio
    async def test_core_pipeline_no_speaker_in_output(self, tmp_path: Path) -> None:
        """core profile output must not include speaker data."""
        from dalston.orchestrator.lite_main import build_default_pipeline

        pipeline = build_default_pipeline()
        result = await pipeline.run_job(b"audio-bytes")

        transcript_path = Path(result["transcript_uri"].removeprefix("file://"))
        data = json.loads(transcript_path.read_text())
        assert "speakers" not in data

    @pytest.mark.asyncio
    async def test_core_pipeline_rejects_diarize_at_runtime(
        self, tmp_path: Path
    ) -> None:
        """Validation inside run_job must also catch unsupported features."""
        from dalston.orchestrator.lite_main import build_default_pipeline

        pipeline = build_default_pipeline()
        with pytest.raises(LiteUnsupportedFeatureError):
            await pipeline.run_job(
                b"audio-bytes",
                parameters={"speaker_detection": "diarize"},
            )
