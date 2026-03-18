"""Root test configuration: shared autouse fixtures for cross-test isolation.

These fixtures prevent the most common sources of test-ordering pollution:

1. ``os.environ`` snapshot/restore — any test (or the code it invokes, such
   as the CLI via ``CliRunner``) that mutates ``os.environ`` will have those
   changes rolled back automatically.  This covers direct assignments
   (``os.environ["KEY"] = ...``), ``monkeypatch.setenv``, and side effects
   like ``load_dotenv()`` triggered by CLI startup code.

2. ``get_settings`` LRU cache — env-var changes made by one test bleed into
   the next because the cached ``Settings`` instance is never evicted.

3. Lazy DB engine/session globals in ``dalston.db.session`` — an engine
   pointing at one test's ``tmp_path`` SQLite file stays alive and is reused
   by later tests that expect a fresh engine.

All fixtures are ``autouse=True`` with ``function`` scope (the default), so
they wrap every test in the suite without any per-test annotation.

Note on ordering: ``_restore_env`` must run first (outermost), so that when
``_reset_settings_cache`` clears the LRU cache the env is already at baseline.
Pytest runs autouse fixtures in definition order, outermost first.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture(autouse=True)
def _restore_env() -> Generator[None, None, None]:
    """Snapshot ``os.environ`` before each test and restore it afterwards.

    This is the primary guard against env-var pollution.  Without it, any test
    (or library code the test invokes) that calls ``os.environ.__setitem__``,
    ``os.environ.update``, or ``python-dotenv``'s ``load_dotenv()`` will leave
    the process environment permanently modified for every subsequent test.

    The snapshot/restore is O(number of env vars) and adds negligible overhead.
    """
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Generator[None, None, None]:
    """Clear the ``get_settings`` LRU cache before and after each test.

    Prevents settings loaded by one test (via ``monkeypatch.setenv`` or direct
    ``os.environ`` mutation) from leaking into subsequent tests through the
    cached ``Settings`` instance.
    """
    from dalston.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_db_session() -> Generator[None, None, None]:
    """Reset lazy DB engine/session globals before and after each test.

    Prevents a database engine initialised for one test (e.g.
    ``sqlite+aiosqlite`` pointing at a ``tmp_path``) from being reused in
    subsequent tests that expect a fresh engine or a different database URL.
    """
    from dalston.db.session import reset_session_state

    reset_session_state()
    yield
    reset_session_state()


@pytest.fixture(autouse=True)
def _reset_security_manager() -> Generator[None, None, None]:
    """Reset the SecurityManager singleton before and after each test.

    The SecurityManager is a module-level singleton that caches security
    configuration (API key enforcement, admin keys, etc.).  Without this
    reset, a test that initialises the manager with permissive settings
    (e.g. lite mode with no auth) will leak that state into subsequent
    tests that expect authentication to be enforced.
    """
    from dalston.gateway.security.manager import reset_security_manager

    reset_security_manager()
    yield
    reset_security_manager()


@pytest.fixture(autouse=True)
def _default_lite_stub_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests deterministic by avoiding heavyweight local model defaults."""
    monkeypatch.setenv("DALSTON_LITE_TRANSCRIBE_BACKEND", "stub")
    monkeypatch.setenv("DALSTON_LITE_DIARIZE_BACKEND", "stub")


@pytest.fixture
def mock_async_db() -> AsyncMock:
    """AsyncMock DB session with synchronous SQLAlchemy methods correctly mocked.

    SQLAlchemy's ``Session.add()`` and ``Session.expire()`` are synchronous.
    A plain ``AsyncMock`` turns them into coroutines that, when called without
    ``await``, emit ``RuntimeWarning: coroutine … was never awaited``.
    """
    db = AsyncMock()
    db.add = MagicMock()
    db.expire = MagicMock()
    return db
