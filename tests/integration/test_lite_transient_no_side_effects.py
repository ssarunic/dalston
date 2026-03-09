from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from dalston.config import get_settings
from dalston.db.session import reset_session_state
from dalston.gateway.dependencies import get_ingestion_service, reset_rate_limiter
from dalston.gateway.services.audio_probe import AudioMetadata


class _InlinePipeline:
    async def run_job(self, *_args, **_kwargs) -> dict:
        return {
            "transcript": {
                "status": "completed",
                "text": "lite transcript",
                "words": [],
                "segments": [],
                "speakers": [],
                "metadata": {"language": "en"},
            }
        }


def _fake_ingestion_service() -> AsyncMock:
    service = AsyncMock()
    service.ingest.return_value = SimpleNamespace(
        content=b"RIFF",
        filename="test.wav",
        metadata=AudioMetadata(
            format="wav",
            duration=1.0,
            sample_rate=16000,
            channels=1,
            bit_depth=16,
        ),
    )
    return service


def test_lite_transient_request_has_no_disk_side_effects(
    monkeypatch,
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / ".dalston"
    artifacts_dir = state_dir / "artifacts"
    db_file = state_dir / "lite.db"

    monkeypatch.setenv("DALSTON_MODE", "lite")
    monkeypatch.setenv("DALSTON_SECURITY_MODE", "none")
    monkeypatch.setenv("DALSTON_RETENTION_DEFAULT_DAYS", "0")
    monkeypatch.setenv(
        "DALSTON_LITE_DATABASE_URL",
        f"sqlite+aiosqlite:///{db_file}",
    )
    monkeypatch.setenv("DALSTON_LITE_ARTIFACTS_DIR", str(artifacts_dir))

    get_settings.cache_clear()
    reset_session_state()
    reset_rate_limiter()

    from dalston.gateway.main import app

    app.dependency_overrides[get_ingestion_service] = _fake_ingestion_service
    monkeypatch.setattr(
        "dalston.orchestrator.lite_main.build_pipeline",
        lambda **_kwargs: _InlinePipeline(),
    )

    try:
        with TestClient(app) as client:
            response = client.post(
                "/v1/audio/transcriptions",
                files={"file": ("test.wav", BytesIO(b"RIFF"), "audio/wav")},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "completed"
    assert payload["text"] == "lite transcript"
    assert payload["retention"]["mode"] == "none"
    assert not db_file.exists()
    assert not artifacts_dir.exists()
