import pytest

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
async def test_lite_db_bootstrap_creates_all_tables(monkeypatch, tmp_path) -> None:
    """Verify the single consolidated migration creates the expected tables."""
    from sqlalchemy import text

    monkeypatch.setenv("DALSTON_MODE", "lite")
    monkeypatch.setenv(
        "DALSTON_LITE_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/lite.db"
    )
    get_settings.cache_clear()
    reset_session_state()
    await init_db()

    expected_tables = {
        "tenants",
        "api_keys",
        "jobs",
        "tasks",
        "task_dependencies",
        "models",
        "model_languages",
        "webhook_endpoints",
        "webhook_endpoint_events",
        "webhook_deliveries",
        "pii_entity_types",
        "job_pii_entity_types",
        "artifact_objects",
        "artifact_compliance_tags",
        "settings",
        "realtime_sessions",
        "audit_log",
        "alembic_version",
    }

    async with async_session() as session:
        result = await session.execute(
            text("SELECT name FROM sqlite_master WHERE type='table'")
        )
        actual_tables = {row[0] for row in result.fetchall()}

    assert expected_tables.issubset(actual_tables), (
        f"Missing tables: {expected_tables - actual_tables}"
    )


@pytest.mark.asyncio
async def test_lite_db_bootstrap_drops_hybrid_enhance_column(
    monkeypatch, tmp_path
) -> None:
    """Latest schema should not include hybrid-mode enhance_on_end column."""
    from sqlalchemy import text

    monkeypatch.setenv("DALSTON_MODE", "lite")
    monkeypatch.setenv(
        "DALSTON_LITE_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/lite.db"
    )
    get_settings.cache_clear()
    reset_session_state()
    await init_db()

    async with async_session() as session:
        result = await session.execute(text("PRAGMA table_info('realtime_sessions')"))
        columns = {row[1] for row in result.fetchall()}

    assert "enhance_on_end" not in columns


@pytest.mark.asyncio
async def test_lite_db_bootstrap_applies_latest_revision(monkeypatch, tmp_path) -> None:
    """Lite bootstrap should migrate to the current Alembic head revision."""
    from sqlalchemy import text

    monkeypatch.setenv("DALSTON_MODE", "lite")
    monkeypatch.setenv(
        "DALSTON_LITE_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/lite.db"
    )
    get_settings.cache_clear()
    reset_session_state()
    await init_db()

    async with async_session() as session:
        result = await session.execute(text("SELECT version_num FROM alembic_version"))
        revisions = {row[0] for row in result.fetchall()}

    assert revisions == {"0007_rename_task_input_output_uri"}
