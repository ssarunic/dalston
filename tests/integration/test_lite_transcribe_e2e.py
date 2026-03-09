import asyncio
import json
from pathlib import Path

import pytest

from dalston.config import get_settings
from dalston.orchestrator.lite_main import build_default_pipeline


@pytest.mark.asyncio
async def test_lite_transcribe_e2e(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DALSTON_MODE", "lite")
    monkeypatch.setenv("DALSTON_RETENTION_DEFAULT_DAYS", "30")
    monkeypatch.setenv("DALSTON_LITE_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    get_settings.cache_clear()
    pipeline = build_default_pipeline()
    result = await pipeline.run_job(b"audio")
    output_path = Path(result["transcript_uri"].removeprefix("file://"))
    data = json.loads(output_path.read_text())
    assert data["status"] == "completed"
    assert data["text"] == "lite transcript"


@pytest.mark.asyncio
async def test_lite_pipeline_does_not_block_event_loop(monkeypatch, tmp_path) -> None:
    """Stage processing runs in asyncio.to_thread(), so concurrent tasks can progress."""
    monkeypatch.setenv("DALSTON_MODE", "lite")
    monkeypatch.setenv("DALSTON_RETENTION_DEFAULT_DAYS", "30")
    monkeypatch.setenv("DALSTON_LITE_ARTIFACTS_DIR", str(tmp_path / "artifacts2"))
    get_settings.cache_clear()
    pipeline = build_default_pipeline()

    progress: list[str] = []

    async def concurrent_task() -> None:
        for _ in range(5):
            await asyncio.sleep(0)
            progress.append("tick")

    # Run pipeline and concurrent task together; the concurrent task must
    # receive event-loop time while the pipeline stages execute.
    await asyncio.gather(pipeline.run_job(b"audio"), concurrent_task())

    assert len(progress) > 0, "Concurrent task never received event-loop time"
