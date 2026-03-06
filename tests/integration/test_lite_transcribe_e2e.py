import json
from pathlib import Path

import pytest

from dalston.config import get_settings
from dalston.orchestrator.lite_main import build_default_pipeline


@pytest.mark.asyncio
async def test_lite_transcribe_e2e(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DALSTON_MODE", "lite")
    monkeypatch.setenv("DALSTON_LITE_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    get_settings.cache_clear()
    pipeline = build_default_pipeline()
    result = await pipeline.run_job(b"audio")
    output_path = Path(result["transcript_uri"].removeprefix("file://"))
    data = json.loads(output_path.read_text())
    assert data["status"] == "completed"
    assert data["text"] == "lite transcript"
