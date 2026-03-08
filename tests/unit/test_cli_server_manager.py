from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import Mock

import pytest
from dalston_cli.bootstrap import server_manager as sm
from dalston_cli.bootstrap.settings import load_bootstrap_settings


def _settings_for(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return load_bootstrap_settings()


def test_bootstrap_lock_acquire_release(tmp_path: Path) -> None:
    lock_path = tmp_path / "bootstrap.lock"
    with sm._BootstrapLock(lock_path, timeout_seconds=1):
        assert lock_path.exists()
    assert not lock_path.exists()


def test_bootstrap_lock_reclaims_stale_file(tmp_path: Path) -> None:
    lock_path = tmp_path / "bootstrap.lock"
    lock_path.write_text("stale", encoding="utf-8")
    stale_time = time.time() - 30
    os.utime(lock_path, (stale_time, stale_time))

    with sm._BootstrapLock(lock_path, timeout_seconds=1):
        assert lock_path.exists()
    assert not lock_path.exists()


def test_bootstrap_lock_times_out_when_contended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path = tmp_path / "bootstrap.lock"
    lock_path.write_text("active", encoding="utf-8")

    monotonic_values = iter([100.0, 100.0, 101.1, 101.1])
    monkeypatch.setattr(sm.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(sm.time, "sleep", lambda _seconds: None)

    with pytest.raises(sm.ServerBootstrapError, match="Timed out waiting"):
        with sm._BootstrapLock(lock_path, timeout_seconds=1):
            pass


def test_probe_local_server_ready(monkeypatch, tmp_path: Path) -> None:
    settings = _settings_for(tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "_is_dalston_healthy", lambda _url: True)

    result = sm.probe_local_server(base_url="http://127.0.0.1:8000", settings=settings)

    assert result.state == sm.ServerProbeState.READY


def test_is_dalston_healthy_handles_non_json_response(monkeypatch) -> None:
    class _Response:
        status_code = 200

        def json(self):
            raise ValueError("not json")

    monkeypatch.setattr(sm.httpx, "get", lambda *_args, **_kwargs: _Response())
    assert sm._is_dalston_healthy("http://127.0.0.1:8000") is False


def test_probe_local_server_port_conflict(monkeypatch, tmp_path: Path) -> None:
    settings = _settings_for(tmp_path, monkeypatch)
    monkeypatch.setattr(sm, "_is_dalston_healthy", lambda _url: False)
    monkeypatch.setattr(sm, "_port_is_open", lambda _h, _p: True)

    result = sm.probe_local_server(base_url="http://127.0.0.1:8000", settings=settings)

    assert result.state == sm.ServerProbeState.PORT_CONFLICT


def test_ensure_local_server_ready_starts_when_missing(
    monkeypatch, tmp_path: Path
) -> None:
    settings = _settings_for(tmp_path, monkeypatch)
    states = [
        sm.ServerProbeResult(sm.ServerProbeState.NOT_RUNNING),
        sm.ServerProbeResult(sm.ServerProbeState.NOT_RUNNING),
    ]
    monkeypatch.setattr(sm, "probe_local_server", lambda **_: states.pop(0))
    monkeypatch.setattr(sm, "_start_detached_server", lambda *_args, **_kwargs: 1234)
    monkeypatch.setattr(sm, "_wait_for_server_ready", lambda **_: None)
    monkeypatch.setattr(sm, "_pid_exists", lambda _pid: False)

    result = sm.ensure_local_server_ready(
        target_url="http://127.0.0.1:8000",
        settings=settings,
    )

    assert result.started is True
    assert result.skipped is False
    assert not settings.lock_file.exists()


def test_ensure_local_server_ready_restarts_when_idle_expired(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = _settings_for(tmp_path, monkeypatch)
    settings = settings.__class__(
        **{**settings.__dict__, "ghost_idle_timeout_seconds": 30}
    )
    metadata = sm.GhostPidMetadata(
        pid=777,
        base_url="http://127.0.0.1:8000",
        started_at="",
        mode="lite",
        security_mode="none",
        idle_timeout_seconds=30,
        last_used_at="2020-01-01T00:00:00+00:00",
    )
    sm._write_pid_metadata(settings.pid_file, metadata)

    states = [
        sm.ServerProbeResult(sm.ServerProbeState.READY),
        sm.ServerProbeResult(sm.ServerProbeState.NOT_RUNNING),
    ]
    monkeypatch.setattr(sm, "probe_local_server", lambda **_: states.pop(0))
    mock_kill = Mock(return_value=None)
    monkeypatch.setattr(sm, "_kill_pid", mock_kill)
    monkeypatch.setattr(sm, "_pid_exists", lambda _pid: True)
    monkeypatch.setattr(sm, "_process_looks_like_ghost", lambda _pid: True)
    monkeypatch.setattr(sm, "_start_detached_server", lambda *_args, **_kwargs: 1234)
    monkeypatch.setattr(sm, "_wait_for_server_ready", lambda **_: None)

    result = sm.ensure_local_server_ready(
        target_url="http://127.0.0.1:8000",
        settings=settings,
    )

    assert result.started is True
    mock_kill.assert_called_once_with(777)


def test_ensure_local_server_ready_restarts_when_idle_expired_and_probe_stays_ready(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = _settings_for(tmp_path, monkeypatch)
    settings = settings.__class__(
        **{**settings.__dict__, "ghost_idle_timeout_seconds": 30}
    )
    metadata = sm.GhostPidMetadata(
        pid=778,
        base_url="http://127.0.0.1:8000",
        started_at="",
        mode="lite",
        security_mode="none",
        idle_timeout_seconds=30,
        last_used_at="2020-01-01T00:00:00+00:00",
    )
    sm._write_pid_metadata(settings.pid_file, metadata)

    states = [
        sm.ServerProbeResult(sm.ServerProbeState.READY),
        sm.ServerProbeResult(sm.ServerProbeState.READY),
    ]
    monkeypatch.setattr(sm, "probe_local_server", lambda **_: states.pop(0))
    mock_kill = Mock(return_value=None)
    monkeypatch.setattr(sm, "_kill_pid", mock_kill)
    monkeypatch.setattr(sm, "_pid_exists", lambda _pid: True)
    monkeypatch.setattr(sm, "_process_looks_like_ghost", lambda _pid: True)
    monkeypatch.setattr(sm, "_start_detached_server", lambda *_args, **_kwargs: 1234)
    monkeypatch.setattr(sm, "_wait_for_server_ready", lambda **_: None)

    result = sm.ensure_local_server_ready(
        target_url="http://127.0.0.1:8000",
        settings=settings,
    )

    assert result.started is True
    mock_kill.assert_called_once_with(778)


def test_ensure_local_server_ready_restarts_from_initial_unhealthy_probe(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = _settings_for(tmp_path, monkeypatch)
    metadata = sm.GhostPidMetadata(
        pid=880,
        base_url="http://127.0.0.1:8000",
        started_at="",
        mode="lite",
        security_mode="none",
        idle_timeout_seconds=900,
        last_used_at="",
    )
    sm._write_pid_metadata(settings.pid_file, metadata)

    states = [
        sm.ServerProbeResult(sm.ServerProbeState.DALSTON_UNHEALTHY),
        sm.ServerProbeResult(sm.ServerProbeState.NOT_RUNNING),
    ]
    monkeypatch.setattr(sm, "probe_local_server", lambda **_: states.pop(0))
    monkeypatch.setattr(sm, "_pid_exists", lambda _pid: True)
    monkeypatch.setattr(sm, "_process_looks_like_ghost", lambda _pid: True)
    mock_kill = Mock(return_value=None)
    monkeypatch.setattr(sm, "_kill_pid", mock_kill)
    monkeypatch.setattr(sm, "_start_detached_server", lambda *_args, **_kwargs: 1234)
    monkeypatch.setattr(sm, "_wait_for_server_ready", lambda **_: None)

    result = sm.ensure_local_server_ready(
        target_url="http://127.0.0.1:8000",
        settings=settings,
    )

    assert result.started is True
    mock_kill.assert_called_once_with(880)


def test_ensure_local_server_ready_does_not_kill_non_ghost_process(
    monkeypatch,
    tmp_path: Path,
) -> None:
    settings = _settings_for(tmp_path, monkeypatch)
    metadata = sm.GhostPidMetadata(
        pid=881,
        base_url="http://127.0.0.1:8000",
        started_at="",
        mode="lite",
        security_mode="none",
        idle_timeout_seconds=900,
        last_used_at="",
    )
    sm._write_pid_metadata(settings.pid_file, metadata)

    states = [
        sm.ServerProbeResult(sm.ServerProbeState.NOT_RUNNING),
        sm.ServerProbeResult(sm.ServerProbeState.NOT_RUNNING),
    ]
    monkeypatch.setattr(sm, "probe_local_server", lambda **_: states.pop(0))
    monkeypatch.setattr(sm, "_pid_exists", lambda _pid: True)
    monkeypatch.setattr(sm, "_process_looks_like_ghost", lambda _pid: False)
    mock_kill = Mock(return_value=None)
    monkeypatch.setattr(sm, "_kill_pid", mock_kill)
    monkeypatch.setattr(sm, "_start_detached_server", lambda *_args, **_kwargs: 1234)
    monkeypatch.setattr(sm, "_wait_for_server_ready", lambda **_: None)

    result = sm.ensure_local_server_ready(
        target_url="http://127.0.0.1:8000",
        settings=settings,
    )

    assert result.started is True
    mock_kill.assert_not_called()


def test_ensure_local_server_ready_raises_port_conflict(
    monkeypatch, tmp_path: Path
) -> None:
    settings = _settings_for(tmp_path, monkeypatch)
    monkeypatch.setattr(
        sm,
        "probe_local_server",
        lambda **_: sm.ServerProbeResult(
            sm.ServerProbeState.PORT_CONFLICT,
            detail="port busy",
        ),
    )

    with pytest.raises(sm.ServerBootstrapError, match="port busy"):
        sm.ensure_local_server_ready(
            target_url="http://127.0.0.1:8000",
            settings=settings,
        )


def test_stop_local_server_cleans_stale_pid(monkeypatch, tmp_path: Path) -> None:
    settings = _settings_for(tmp_path, monkeypatch)
    metadata = sm.GhostPidMetadata(
        pid=999999,
        base_url="http://127.0.0.1:8000",
        started_at="",
        mode="lite",
        security_mode="none",
        idle_timeout_seconds=900,
        last_used_at="",
    )
    sm._write_pid_metadata(settings.pid_file, metadata)
    monkeypatch.setattr(sm, "_pid_exists", lambda _pid: False)

    stopped = sm.stop_local_server(settings=settings)

    assert stopped is True
    assert not settings.pid_file.exists()


def test_stop_local_server_noop_without_pid(monkeypatch, tmp_path: Path) -> None:
    settings = _settings_for(tmp_path, monkeypatch)
    stopped = sm.stop_local_server(settings=settings)
    assert stopped is False


def test_process_looks_like_ghost_uses_linux_procfs(monkeypatch) -> None:
    monkeypatch.setattr(sm.sys, "platform", "linux")
    monkeypatch.setattr(
        sm.Path,
        "read_bytes",
        lambda _self: b"python\x00-m\x00uvicorn\x00dalston.gateway.main:app\x00",
    )

    assert sm._process_looks_like_ghost(12345) is True


# ---------------------------------------------------------------------------
# _reclaim_stale_lock hardening (Issue #2)
# ---------------------------------------------------------------------------


def test_reclaim_stale_lock_reclaims_when_holder_dead(tmp_path: Path) -> None:
    """Stale lock from a dead PID is reclaimed atomically."""
    lock_path = tmp_path / "bootstrap.lock"
    payload = '{"pid": 999999999, "acquired_at": "2020-01-01T00:00:00+00:00"}'
    lock_path.write_text(payload, encoding="utf-8")
    # Make file old enough to be expired.
    old_time = sm.time.time() - 9999
    sm.os.utime(lock_path, (old_time, old_time))

    lock = sm._BootstrapLock(lock_path, timeout_seconds=30)
    result = lock._reclaim_stale_lock()

    assert result is True
    assert lock._acquired is True
    assert lock_path.exists()
    # Clean up
    lock.__exit__(None, None, None)


def test_reclaim_stale_lock_does_not_reclaim_live_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lock held by a live PID within timeout is not reclaimed."""
    lock_path = tmp_path / "bootstrap.lock"
    own_pid = sm.os.getpid()
    payload = f'{{"pid": {own_pid}, "acquired_at": "2099-01-01T00:00:00+00:00"}}'
    lock_path.write_text(payload, encoding="utf-8")

    lock = sm._BootstrapLock(lock_path, timeout_seconds=9999)
    result = lock._reclaim_stale_lock()

    assert result is False
    assert lock._acquired is False


def test_reclaim_stale_lock_reclaims_corrupt_file(tmp_path: Path) -> None:
    """Old corrupt (unparseable) lock file is treated as abandoned and reclaimed."""
    lock_path = tmp_path / "bootstrap.lock"
    lock_path.write_text("NOT_VALID_JSON{{{{", encoding="utf-8")
    # Age the file past the timeout so the mtime fallback allows reclaim.
    old_time = sm.time.time() - 9999
    sm.os.utime(lock_path, (old_time, old_time))

    lock = sm._BootstrapLock(lock_path, timeout_seconds=30)
    result = lock._reclaim_stale_lock()

    assert result is True
    assert lock._acquired is True
    lock.__exit__(None, None, None)


def test_reclaim_stale_lock_does_not_reclaim_fresh_corrupt_file(tmp_path: Path) -> None:
    """Fresh corrupt lock file is not reclaimed (may still be written by holder)."""
    lock_path = tmp_path / "bootstrap.lock"
    lock_path.write_text("NOT_VALID_JSON{{{{", encoding="utf-8")
    # mtime is current (just written) — within the timeout window.

    lock = sm._BootstrapLock(lock_path, timeout_seconds=9999)
    result = lock._reclaim_stale_lock()

    assert result is False
    assert lock._acquired is False


def test_reclaim_stale_lock_reclaims_missing_pid_field(tmp_path: Path) -> None:
    """Old lock file with valid JSON but missing pid field is reclaimed."""
    lock_path = tmp_path / "bootstrap.lock"
    lock_path.write_text('{"acquired_at": "2020-01-01"}', encoding="utf-8")
    old_time = sm.time.time() - 9999
    sm.os.utime(lock_path, (old_time, old_time))

    lock = sm._BootstrapLock(lock_path, timeout_seconds=30)
    result = lock._reclaim_stale_lock()

    assert result is True
    assert lock._acquired is True
    lock.__exit__(None, None, None)


def test_reclaim_stale_lock_concurrent_loses_rename_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If os.rename raises (concurrent reclaim), returns False gracefully."""
    lock_path = tmp_path / "bootstrap.lock"
    lock_path.write_text('{"pid": 999999999}', encoding="utf-8")
    old_time = sm.time.time() - 9999
    sm.os.utime(lock_path, (old_time, old_time))

    original_rename = sm.os.rename

    def fail_rename(src, dst):
        raise OSError("simulated concurrent rename failure")

    monkeypatch.setattr(sm.os, "rename", fail_rename)

    lock = sm._BootstrapLock(lock_path, timeout_seconds=30)
    result = lock._reclaim_stale_lock()

    assert result is False
    assert lock._acquired is False

    monkeypatch.setattr(sm.os, "rename", original_rename)
