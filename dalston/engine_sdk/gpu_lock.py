"""Per-host GPU lock using Redis.

When multiple GPU engines share the same physical GPU (identified by
``node_id``), only one engine can run inference at a time. This module
provides a simple Redis-based lock that serializes GPU access across
co-located containers while allowing true parallelism when engines are
on separate hosts.

CPU-only engines skip the lock entirely.

The lock uses a short TTL (30s) with a background heartbeat thread that
extends it every 10s. If the process crashes, the thread dies and the
lock auto-expires in ≤30s.
"""

from __future__ import annotations

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
HEARTBEAT_INTERVAL = 10.0
LOCK_RETRY_INTERVAL = 2.0

KEY_PREFIX = "dalston:gpu_lock:"

# Lua script: atomically extend TTL only if we still own the lock.
_EXTEND_SCRIPT = "if redis.call('get',KEYS[1])==ARGV[1] then return redis.call('expire',KEYS[1],ARGV[2]) else return 0 end"

# Lua script: atomically delete only if we still own the lock.
_RELEASE_SCRIPT = "if redis.call('get',KEYS[1])==ARGV[1] then return redis.call('del',KEYS[1]) else return 0 end"


def _heartbeat_loop(
    redis_client: redis.Redis,
    key: str,
    holder: str,
    stop: threading.Event,
) -> None:
    """Extend lock TTL periodically until stop is set."""
    while not stop.wait(HEARTBEAT_INTERVAL):
        try:
            redis_client.eval(_EXTEND_SCRIPT, 1, key, holder, str(LOCK_TTL_SECONDS))
        except Exception:
            logger.warning("gpu_lock_heartbeat_error", lock_key=key, exc_info=True)


@contextmanager
def gpu_lock(
    redis_client: redis.Redis,
    holder: str,
    device: str,
    host_id: str,
    timeout: float = 600.0,
) -> Iterator[float]:
    """Acquire the per-host GPU lock, yield remaining time, then release.

    Args:
        redis_client: Redis connection (sync).
        holder: Unique identifier for this engine instance (used as lock value).
        device: The inference device ("cuda", "cpu", "mps").
            Lock is only acquired for "cuda".
        host_id: Host identifier for scoping the lock (from node_identity).
        timeout: Max seconds to wait for the lock before raising TimeoutError.

    Yields:
        Remaining seconds after lock acquisition (timeout minus wait time).
        For non-CUDA devices, yields the full timeout unchanged.
    """
    if device != "cuda":
        yield timeout
        return

    key = f"{KEY_PREFIX}{host_id}"
    start = time.monotonic()
    deadline = start + timeout
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

        heartbeat = threading.Thread(
            target=_heartbeat_loop,
            args=(redis_client, key, holder, stop_heartbeat),
            daemon=True,
        )
        heartbeat.start()

        remaining = deadline - time.monotonic()
        yield max(remaining, 1.0)
    finally:
        stop_heartbeat.set()
        if acquired:
            try:
                released = redis_client.eval(_RELEASE_SCRIPT, 1, key, holder)
                if released:
                    logger.info("gpu_lock_released", lock_key=key, holder=holder)
                else:
                    logger.warning("gpu_lock_expired_before_release", lock_key=key)
            except Exception:
                logger.warning("gpu_lock_release_error", lock_key=key, exc_info=True)
