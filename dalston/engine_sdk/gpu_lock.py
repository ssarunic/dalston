"""Per-host GPU lock using Redis.

When multiple GPU engines share the same physical GPU (identified by
``DALSTON_HOST_HOSTNAME``), only one engine can run inference at a time.
This module provides a simple Redis-based lock that serializes GPU access
across co-located containers while allowing true parallelism when engines
are on separate hosts.

CPU-only engines skip the lock entirely.

The lock uses a short TTL (30s) with a background heartbeat thread that
extends it every 10s. If the process crashes, the thread dies and the
lock auto-expires in ≤30s.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    import redis

logger = structlog.get_logger()

LOCK_TTL_SECONDS = 30
HEARTBEAT_INTERVAL = 10.0  # extend TTL every 10s
LOCK_RETRY_INTERVAL = 2.0

KEY_PREFIX = "dalston:gpu_lock:"


def _host_id() -> str:
    """Return the host identifier for lock scoping."""
    return os.environ.get("DALSTON_HOST_HOSTNAME") or socket.gethostname()


def _heartbeat_loop(
    redis_client: redis.Redis,
    key: str,
    holder: str,
    stop: threading.Event,
) -> None:
    """Extend lock TTL periodically until stop is set."""
    while not stop.wait(HEARTBEAT_INTERVAL):
        try:
            # Only extend if we still own the lock
            if redis_client.get(key) == holder:
                redis_client.expire(key, LOCK_TTL_SECONDS)
        except Exception:
            logger.warning("gpu_lock_heartbeat_error", lock_key=key, exc_info=True)


@contextmanager
def gpu_lock(
    redis_client: redis.Redis,
    holder: str,
    device: str,
    timeout: float = 600.0,
) -> Iterator[None]:
    """Acquire the per-host GPU lock, yield, then release.

    Args:
        redis_client: Redis connection (sync).
        holder: Unique identifier for this engine instance (used as lock value).
        device: The inference device ("cuda", "cpu", "mps").
            Lock is only acquired for "cuda".
        timeout: Max seconds to wait for the lock before raising TimeoutError.
    """
    if device != "cuda":
        yield
        return

    key = f"{KEY_PREFIX}{_host_id()}"
    deadline = time.monotonic() + timeout
    acquired = False
    stop_heartbeat = threading.Event()

    try:
        while time.monotonic() < deadline:
            acquired = redis_client.set(key, holder, nx=True, ex=LOCK_TTL_SECONDS)
            if acquired:
                logger.info("gpu_lock_acquired", lock_key=key, holder=holder)
                break
            time.sleep(LOCK_RETRY_INTERVAL)
        else:
            raise TimeoutError(
                f"Could not acquire GPU lock {key} within {timeout:.0f}s"
            )

        # Start heartbeat to keep the lock alive
        heartbeat = threading.Thread(
            target=_heartbeat_loop,
            args=(redis_client, key, holder, stop_heartbeat),
            daemon=True,
        )
        heartbeat.start()

        yield
    finally:
        stop_heartbeat.set()
        if acquired:
            # Only release if we still own the lock
            try:
                if redis_client.get(key) == holder:
                    redis_client.delete(key)
                    logger.info("gpu_lock_released", lock_key=key, holder=holder)
            except Exception:
                logger.warning("gpu_lock_release_error", lock_key=key, exc_info=True)
