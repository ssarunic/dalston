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


@pytest.mark.asyncio
async def test_lite_storage_service_prefix_and_task_helpers(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("DALSTON_MODE", "lite")
    monkeypatch.setenv("DALSTON_LITE_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    get_settings.cache_clear()
    settings = get_settings()
    service = StorageService(settings)
    job_id = "00000000-0000-0000-0000-000000000321"
    task_id = "11111111-1111-1111-1111-111111111111"

    root = Path(settings.lite_artifacts_dir)
    audio = root / f"jobs/{job_id}/audio/original.wav"
    task_input = root / f"jobs/{job_id}/tasks/{task_id}/input.json"
    task_output = root / f"jobs/{job_id}/tasks/{task_id}/output.json"
    transcript = root / f"jobs/{job_id}/transcript.json"
    for path in (audio, task_input, task_output, transcript):
        path.parent.mkdir(parents=True, exist_ok=True)

    audio.write_bytes(b"audio")
    task_input.write_text(json.dumps({"in": True}))
    task_output.write_text(json.dumps({"out": True}))
    transcript.write_text(json.dumps({"final": True}))

    assert await service.has_audio(job_id)  # type: ignore[arg-type]
    assert await service.get_task_input(  # type: ignore[arg-type]
        job_id, task_id
    ) == {"in": True}
    assert await service.get_task_output(  # type: ignore[arg-type]
        job_id, task_id
    ) == {"out": True}
    assert await service.object_exists(  # type: ignore[arg-type]
        f"jobs/{job_id}/audio/original.wav"
    )

    await service.delete_job_audio(job_id)  # type: ignore[arg-type]
    assert not await service.has_audio(job_id)  # type: ignore[arg-type]
    assert (root / f"jobs/{job_id}/transcript.json").exists()

    await service.delete_job_artifacts(job_id)  # type: ignore[arg-type]
    assert not (root / f"jobs/{job_id}").exists()
