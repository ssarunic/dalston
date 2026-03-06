import pytest
from sqlalchemy import text

from dalston.config import get_settings
from dalston.db.session import async_session, init_db, reset_session_state


@pytest.mark.asyncio
async def test_lite_db_bootstrap_creates_schema(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("DALSTON_MODE", "lite")
    monkeypatch.setenv(
        "DALSTON_LITE_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/lite.db"
    )
    get_settings.cache_clear()
    reset_session_state()
    await init_db()


@pytest.mark.asyncio
async def test_lite_db_bootstrap_creates_missing_parent_dirs(
    monkeypatch, tmp_path
) -> None:
    db_path = tmp_path / "nested" / "state" / "lite.db"
    monkeypatch.setenv("DALSTON_MODE", "lite")
    monkeypatch.setenv("DALSTON_LITE_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")

    get_settings.cache_clear()
    reset_session_state()
    await init_db()

    assert db_path.exists()


@pytest.mark.asyncio
async def test_lite_db_bootstrap_upgrades_minimal_legacy_schema(
    monkeypatch, tmp_path
) -> None:
    import sqlite3

    db_path = tmp_path / "legacy" / "lite.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE tenants (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                settings TEXT NOT NULL DEFAULT '{}',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE jobs (
                id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                status TEXT NOT NULL,
                audio_uri TEXT NOT NULL,
                parameters TEXT NOT NULL DEFAULT '{}',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                completed_at TEXT,
                error TEXT,
                FOREIGN KEY(tenant_id) REFERENCES tenants(id)
            );
            CREATE TABLE tasks (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                status TEXT NOT NULL,
                runtime TEXT NOT NULL,
                config TEXT NOT NULL DEFAULT '{}',
                output_uri TEXT,
                FOREIGN KEY(job_id) REFERENCES jobs(id)
            );
            """
        )

    monkeypatch.setenv("DALSTON_MODE", "lite")
    monkeypatch.setenv("DALSTON_LITE_DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    get_settings.cache_clear()
    reset_session_state()

    await init_db()

    async with async_session() as session:
        jobs_cols_result = await session.execute(text("PRAGMA table_info(jobs)"))
        jobs_cols = {str(row[1]) for row in jobs_cols_result.fetchall()}
        assert "display_name" in jobs_cols
        assert "started_at" in jobs_cols
        assert "created_by_key_id" in jobs_cols

        tasks_cols_result = await session.execute(text("PRAGMA table_info(tasks)"))
        tasks_cols = {str(row[1]) for row in tasks_cols_result.fetchall()}
        assert "dependencies" in tasks_cols
        assert "input_uri" in tasks_cols
        assert "retries" in tasks_cols
        assert "max_retries" in tasks_cols
        assert "required" in tasks_cols
        assert "error" in tasks_cols
        assert "started_at" in tasks_cols
        assert "completed_at" in tasks_cols

        models_table_result = await session.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name='models'")
        )
        assert models_table_result.scalar_one_or_none() == "models"
