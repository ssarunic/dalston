"""Environment-backed settings for CLI bootstrap flow."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

_TRUE_VALUES = {"1", "true", "yes", "y", "on"}
_FALSE_VALUES = {"0", "false", "no", "n", "off"}

DEFAULT_LOCAL_SERVER_URL = "http://127.0.0.1:8000"
DEFAULT_MODEL_ID = "distil-small"


def _parse_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default

    lowered = value.strip().lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    return default


def _parse_int(value: str | None, default: int, minimum: int) -> int:
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed >= minimum else default


@dataclass(frozen=True)
class BootstrapSettings:
    """Typed bootstrap settings derived from environment variables."""

    enabled: bool
    default_model: str
    local_server_url: str
    server_start_timeout_seconds: int
    bootstrap_lock_timeout_seconds: int
    ghost_idle_timeout_seconds: int
    model_ensure_timeout_seconds: int
    min_free_bytes: int
    run_dir: Path
    log_dir: Path
    pid_file: Path
    lock_file: Path
    log_file: Path

    def target_is_local(self, target_url: str) -> bool:
        """Return True if URL resolves to a localhost endpoint."""
        parsed = urlparse(target_url)
        host = (parsed.hostname or "").lower()
        return host in {"localhost", "127.0.0.1", "::1"}


def load_bootstrap_settings(server_url: str | None = None) -> BootstrapSettings:
    """Load bootstrap settings from environment with deterministic defaults."""
    home = Path.home() / ".dalston"
    run_dir = home / "run"
    log_dir = home / "logs"

    local_server_url = os.getenv(
        "DALSTON_LOCAL_SERVER_URL",
        server_url or DEFAULT_LOCAL_SERVER_URL,
    )

    return BootstrapSettings(
        enabled=_parse_bool(os.getenv("DALSTON_BOOTSTRAP"), default=True),
        default_model=os.getenv("DALSTON_DEFAULT_MODEL", DEFAULT_MODEL_ID),
        local_server_url=local_server_url,
        server_start_timeout_seconds=_parse_int(
            os.getenv("DALSTON_SERVER_START_TIMEOUT_SECONDS"),
            default=30,
            minimum=1,
        ),
        bootstrap_lock_timeout_seconds=_parse_int(
            os.getenv("DALSTON_BOOTSTRAP_LOCK_TIMEOUT_SECONDS"),
            default=30,
            minimum=1,
        ),
        ghost_idle_timeout_seconds=_parse_int(
            os.getenv("DALSTON_GHOST_IDLE_TIMEOUT_SECONDS"),
            default=900,
            minimum=30,
        ),
        model_ensure_timeout_seconds=_parse_int(
            os.getenv("DALSTON_MODEL_ENSURE_TIMEOUT_SECONDS"),
            default=900,
            minimum=1,
        ),
        min_free_bytes=_parse_int(
            os.getenv("DALSTON_BOOTSTRAP_MIN_FREE_BYTES"),
            default=256 * 1024 * 1024,
            minimum=0,
        ),
        run_dir=run_dir,
        log_dir=log_dir,
        pid_file=run_dir / "ghost-server.pid",
        lock_file=run_dir / "bootstrap.lock",
        log_file=log_dir / "ghost-server.log",
    )
