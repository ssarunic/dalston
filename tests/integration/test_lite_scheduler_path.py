import pytest

from dalston.config import get_settings
from dalston.orchestrator.lite_main import build_default_pipeline


@pytest.mark.asyncio
async def test_lite_scheduler_prepare_to_merge(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DALSTON_MODE", "lite")
    monkeypatch.setenv("DALSTON_RETENTION_DEFAULT_DAYS", "30")
    monkeypatch.setenv("DALSTON_LITE_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    get_settings.cache_clear()
    pipeline = build_default_pipeline()
    result = await pipeline.run_job(b"audio-bytes")
    assert result["transcript_uri"].startswith("file://")
