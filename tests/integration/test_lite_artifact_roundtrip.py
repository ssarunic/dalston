import json
from pathlib import Path

import pytest

from dalston.config import get_settings
from dalston.gateway.services.storage import StorageService


@pytest.mark.asyncio
async def test_lite_storage_service_roundtrip(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DALSTON_MODE", "lite")
    monkeypatch.setenv("DALSTON_LITE_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    get_settings.cache_clear()
    settings = get_settings()
    service = StorageService(settings)
    job_id = "00000000-0000-0000-0000-000000000123"
    p = Path(settings.lite_artifacts_dir) / f"jobs/{job_id}/transcript.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"ok": True}))
    transcript = await service.get_transcript(job_id)  # type: ignore[arg-type]
    assert transcript == {"ok": True}
