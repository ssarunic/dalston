"""Local ghost-server lifecycle management for CLI bootstrap."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from dalston_cli.bootstrap.settings import BootstrapSettings

LOCK_POLL_INTERVAL_SECONDS = 0.1


class ServerBootstrapError(RuntimeError):
    """Base error for local server bootstrap failures."""

    def __init__(self, message: str, remediation: str | None = None):
        super().__init__(message)
        self.remediation = remediation


class ServerProbeState(str, Enum):
    READY = "ready"
    NOT_RUNNING = "not_running"
    DALSTON_UNHEALTHY = "dalston_unhealthy"
    PORT_CONFLICT = "port_conflict"


@dataclass(frozen=True)
class ServerProbeResult:
    """Result of probing a local server endpoint."""

    state: ServerProbeState
    detail: str = ""


@dataclass(frozen=True)
class ServerReadyResult:
    """Result from ensuring local server readiness."""

    started: bool
    skipped: bool
    managed: bool


@dataclass(frozen=True)
class GhostPidMetadata:
    """PID metadata persisted by ghost server manager."""

    pid: int
    base_url: str
    started_at: str
    mode: str
    security_mode: str
    idle_timeout_seconds: int
    last_used_at: str


class _BootstrapLock:
    """Filesystem lock using O_EXCL create semantics."""

    def __init__(self, path: Path, timeout_seconds: int):
        self._path = path
        self._timeout_seconds = timeout_seconds
        self._acquired = False

    def _is_stale(self) -> bool:
        if not self._path.exists():
            return False
        age = time.time() - self._path.stat().st_mtime
        return age > self._timeout_seconds

    def __enter__(self) -> _BootstrapLock:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self._timeout_seconds

        while True:
            try:
                fd = os.open(
                    self._path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
            except FileExistsError:
                if self._is_stale():
                    self._path.unlink(missing_ok=True)
                    continue
                if time.monotonic() >= deadline:
                    raise ServerBootstrapError(
                        "Timed out waiting for bootstrap lock.",
                        remediation="Retry after another bootstrap operation completes.",
                    ) from None
                time.sleep(LOCK_POLL_INTERVAL_SECONDS)
                continue

            with os.fdopen(fd, "w", encoding="utf-8") as lock_file:
                payload = {
                    "pid": os.getpid(),
                    "acquired_at": datetime.now(UTC).isoformat(),
                }
                lock_file.write(json.dumps(payload))
            self._acquired = True
            return self

    def __exit__(self, *_: Any) -> None:
        if self._acquired:
            self._path.unlink(missing_ok=True)
            self._acquired = False


def _extract_host_port(base_url: str) -> tuple[str, int]:
    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    if parsed.port:
        return host, parsed.port
    return host, 443 if parsed.scheme == "https" else 80


def _port_is_open(host: str, port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.25)
    try:
        return sock.connect_ex((host, port)) == 0
    finally:
        sock.close()


def _is_dalston_healthy(base_url: str) -> bool:
    try:
        response = httpx.get(f"{base_url.rstrip('/')}/health", timeout=1.0)
    except httpx.HTTPError:
        return False
    if response.status_code != 200:
        return False
    try:
        payload = response.json()
    except ValueError:
        return False
    return payload.get("status") == "healthy"


def _read_pid_metadata(path: Path) -> GhostPidMetadata | None:
    if not path.exists():
        return None

    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return None

    # Backward compatibility with plain integer PID file.
    if raw.isdigit():
        return GhostPidMetadata(
            pid=int(raw),
            base_url="",
            started_at="",
            mode="lite",
            security_mode="none",
            idle_timeout_seconds=900,
            last_used_at="",
        )

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None

    pid = data.get("pid")
    if not isinstance(pid, int):
        return None

    return GhostPidMetadata(
        pid=pid,
        base_url=str(data.get("base_url", "")),
        started_at=str(data.get("started_at", "")),
        mode=str(data.get("mode", "lite")),
        security_mode=str(data.get("security_mode", "none")),
        idle_timeout_seconds=int(data.get("idle_timeout_seconds", 900)),
        last_used_at=str(data.get("last_used_at", "")),
    )


def _write_pid_metadata(path: Path, metadata: GhostPidMetadata) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "pid": metadata.pid,
                "base_url": metadata.base_url,
                "started_at": metadata.started_at,
                "mode": metadata.mode,
                "security_mode": metadata.security_mode,
                "idle_timeout_seconds": metadata.idle_timeout_seconds,
                "last_used_at": metadata.last_used_at,
            }
        ),
        encoding="utf-8",
    )


def _touch_pid_metadata(path: Path, metadata: GhostPidMetadata) -> None:
    _write_pid_metadata(
        path,
        GhostPidMetadata(
            pid=metadata.pid,
            base_url=metadata.base_url,
            started_at=metadata.started_at,
            mode=metadata.mode,
            security_mode=metadata.security_mode,
            idle_timeout_seconds=metadata.idle_timeout_seconds,
            last_used_at=datetime.now(UTC).isoformat(),
        ),
    )


def _is_idle_expired(metadata: GhostPidMetadata, timeout_seconds: int) -> bool:
    if timeout_seconds <= 0:
        return False
    if not metadata.last_used_at:
        return False
    try:
        last_used = datetime.fromisoformat(metadata.last_used_at)
    except ValueError:
        return False
    if last_used.tzinfo is None:
        last_used = last_used.replace(tzinfo=UTC)
    idle_seconds = (datetime.now(UTC) - last_used.astimezone(UTC)).total_seconds()
    return idle_seconds >= timeout_seconds


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _process_looks_like_ghost(pid: int) -> bool:
    cmd = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        capture_output=True,
        text=True,
        check=False,
    )
    command = (cmd.stdout or "").strip()
    if not command:
        return False
    return "uvicorn" in command and "dalston.gateway.main:app" in command


def _kill_pid(pid: int, timeout_seconds: float = 5.0) -> None:
    if not _pid_exists(pid):
        return

    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _pid_exists(pid):
            return
        time.sleep(0.1)

    if _pid_exists(pid):
        os.kill(pid, signal.SIGKILL)


def _tail_log(path: Path, line_count: int = 15) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-line_count:])


def _start_detached_server(base_url: str, settings: BootstrapSettings) -> int:
    host, port = _extract_host_port(base_url)
    settings.log_file.parent.mkdir(parents=True, exist_ok=True)
    log_handle = settings.log_file.open("a", encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "DALSTON_MODE": "lite",
            "DALSTON_SECURITY_MODE": "none",
            "DALSTON_GHOST_IDLE_TIMEOUT_SECONDS": str(
                settings.ghost_idle_timeout_seconds
            ),
        }
    )
    process = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-m",
            "uvicorn",
            "dalston.gateway.main:app",
            "--host",
            host,
            "--port",
            str(port),
        ],
        stdout=log_handle,
        stderr=log_handle,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
    )
    log_handle.close()

    metadata = GhostPidMetadata(
        pid=process.pid,
        base_url=base_url,
        started_at=datetime.now(UTC).isoformat(),
        mode="lite",
        security_mode="none",
        idle_timeout_seconds=settings.ghost_idle_timeout_seconds,
        last_used_at=datetime.now(UTC).isoformat(),
    )
    _write_pid_metadata(settings.pid_file, metadata)
    return process.pid


def _wait_for_server_ready(
    *,
    base_url: str,
    pid: int,
    settings: BootstrapSettings,
) -> None:
    deadline = time.monotonic() + settings.server_start_timeout_seconds
    while time.monotonic() < deadline:
        if _is_dalston_healthy(base_url):
            return
        if not _pid_exists(pid):
            tail = _tail_log(settings.log_file)
            hint = "Review ghost-server.log for details."
            if tail:
                hint = f"Last log lines:\n{tail}"
            raise ServerBootstrapError(
                "Local Dalston server exited during startup.",
                remediation=hint,
            )
        time.sleep(0.25)

    tail = _tail_log(settings.log_file)
    hint = "Increase DALSTON_SERVER_START_TIMEOUT_SECONDS or inspect ghost-server.log."
    if tail:
        hint = f"{hint}\nLast log lines:\n{tail}"
    raise ServerBootstrapError("Timed out waiting for local server readiness.", hint)


def probe_local_server(
    *, base_url: str, settings: BootstrapSettings
) -> ServerProbeResult:
    """Classify current ownership/health state for local endpoint."""
    if _is_dalston_healthy(base_url):
        return ServerProbeResult(ServerProbeState.READY)

    host, port = _extract_host_port(base_url)
    if _port_is_open(host, port):
        metadata = _read_pid_metadata(settings.pid_file)
        if (
            metadata
            and _pid_exists(metadata.pid)
            and _process_looks_like_ghost(metadata.pid)
        ):
            return ServerProbeResult(
                ServerProbeState.DALSTON_UNHEALTHY,
                detail=f"Ghost process PID {metadata.pid} is unhealthy.",
            )
        return ServerProbeResult(
            ServerProbeState.PORT_CONFLICT,
            detail=f"Port {port} is in use by a non-Dalston process.",
        )

    metadata = _read_pid_metadata(settings.pid_file)
    if metadata and not _pid_exists(metadata.pid):
        settings.pid_file.unlink(missing_ok=True)
    return ServerProbeResult(ServerProbeState.NOT_RUNNING)


def ensure_local_server_ready(
    *,
    target_url: str,
    settings: BootstrapSettings,
) -> ServerReadyResult:
    """Ensure local ghost server is running and healthy when target is localhost."""
    if not settings.target_is_local(target_url):
        return ServerReadyResult(started=False, skipped=True, managed=False)

    probe = probe_local_server(base_url=target_url, settings=settings)
    metadata = _read_pid_metadata(settings.pid_file)
    managed = bool(
        metadata
        and _pid_exists(metadata.pid)
        and _process_looks_like_ghost(metadata.pid)
    )
    if (
        probe.state == ServerProbeState.READY
        and managed
        and _is_idle_expired(metadata, settings.ghost_idle_timeout_seconds)
    ):
        probe = ServerProbeResult(ServerProbeState.DALSTON_UNHEALTHY)
    elif probe.state == ServerProbeState.READY:
        if metadata and managed:
            _touch_pid_metadata(settings.pid_file, metadata)
        elif metadata and not _pid_exists(metadata.pid):
            settings.pid_file.unlink(missing_ok=True)
        return ServerReadyResult(started=False, skipped=False, managed=managed)
    if probe.state == ServerProbeState.PORT_CONFLICT:
        raise ServerBootstrapError(
            probe.detail or "Local port is occupied by a non-Dalston process.",
            remediation="Use --server for a different endpoint or free the local port.",
        )

    with _BootstrapLock(settings.lock_file, settings.bootstrap_lock_timeout_seconds):
        probe = probe_local_server(base_url=target_url, settings=settings)
        metadata = _read_pid_metadata(settings.pid_file)
        managed = bool(
            metadata
            and _pid_exists(metadata.pid)
            and _process_looks_like_ghost(metadata.pid)
        )
        if (
            probe.state == ServerProbeState.READY
            and managed
            and _is_idle_expired(metadata, settings.ghost_idle_timeout_seconds)
        ):
            probe = ServerProbeResult(ServerProbeState.DALSTON_UNHEALTHY)
        elif probe.state == ServerProbeState.READY:
            if metadata and managed:
                _touch_pid_metadata(settings.pid_file, metadata)
            elif metadata and not _pid_exists(metadata.pid):
                settings.pid_file.unlink(missing_ok=True)
            return ServerReadyResult(started=False, skipped=False, managed=managed)
        if probe.state == ServerProbeState.PORT_CONFLICT:
            raise ServerBootstrapError(
                probe.detail or "Local port is occupied by a non-Dalston process.",
                remediation="Use --server for a different endpoint or free the local port.",
            )

        if metadata and _pid_exists(metadata.pid):
            _kill_pid(metadata.pid)
        settings.pid_file.unlink(missing_ok=True)

        pid = _start_detached_server(target_url, settings)
        _wait_for_server_ready(base_url=target_url, pid=pid, settings=settings)
        return ServerReadyResult(started=True, skipped=False, managed=True)


def stop_local_server(*, settings: BootstrapSettings) -> bool:
    """Stop ghost server started by CLI bootstrap."""
    metadata = _read_pid_metadata(settings.pid_file)
    if metadata is None:
        return False

    if _pid_exists(metadata.pid) and _process_looks_like_ghost(metadata.pid):
        _kill_pid(metadata.pid)
    settings.pid_file.unlink(missing_ok=True)
    return True
