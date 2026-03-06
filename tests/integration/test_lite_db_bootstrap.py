import pytest

from dalston.config import get_settings
from dalston.db.session import init_db, reset_session_state


@pytest.mark.asyncio
async def test_lite_db_bootstrap_creates_schema(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DALSTON_MODE", "lite")
    monkeypatch.setenv(
        "DALSTON_LITE_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/lite.db"
    )
    get_settings.cache_clear()
    reset_session_state()
    await init_db()
