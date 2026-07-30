"""Postgres -> Redis mirror for autoscale settings overrides (M95).

Postgres is the truth; the dalston:autoscale:overrides hash is a mirror the
control-plane tick reads (it has no Postgres access). The mirror converges
two ways: best-effort sync right after a settings write (for an honest
redis_synced response), and a 60s reconcile loop that rewrites the hash
unconditionally — so a failed sync, a flushed Redis, or a Redis restart all
heal within a minute. The rewrite is atomic (MULTI: DEL + HSET + EXEC) so
the tick never observes a half-written hash.

Lifecycle mirrors the SessionCoordinator start/stop pattern in
gateway/main.py: created and started only in distributed mode, cancelled
and awaited on shutdown.
"""

from __future__ import annotations

import asyncio
import contextlib

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from dalston.common.registry import AUTOSCALE_OVERRIDES_KEY
from dalston.gateway.services.settings import SettingsService

logger = structlog.get_logger()

AUTOSCALE_NAMESPACE = "autoscale"
RECONCILE_INTERVAL_S = 60


class AutoscaleOverridesMirror:
    """Owns the atomic overrides-hash rewrite; single owner on purpose —
    a second inline copy of "rewrite hash from Postgres" would drift."""

    def __init__(self, redis: Redis, session_factory) -> None:
        self._redis = redis
        self._session_factory = session_factory
        self._service = SettingsService()
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("autoscale_overrides_mirror_started")

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        logger.info("autoscale_overrides_mirror_stopped")

    async def sync_once(self) -> bool:
        """Rewrite the hash from Postgres. Never raises; False = Redis
        failed and the reconcile loop is the backstop."""
        try:
            async with self._session_factory() as db:
                overrides = await self._read_overrides(db)
            return await self._rewrite_hash(overrides)
        except Exception:
            logger.warning("autoscale_overrides_sync_failed", exc_info=True)
            return False

    async def sync_from_session(self, db: AsyncSession) -> bool:
        """Sync using an existing session (the PATCH handler's, post-commit)."""
        try:
            overrides = await self._read_overrides(db)
            return await self._rewrite_hash(overrides)
        except Exception:
            logger.warning("autoscale_overrides_sync_failed", exc_info=True)
            return False

    async def _read_overrides(self, db: AsyncSession) -> dict[str, str]:
        raw = await self._service.get_raw_overrides(db, AUTOSCALE_NAMESPACE)
        # Plain strings, not JSON — the tick parses them with the same
        # int() coercion ShapePolicy.from_dict applies to YAML values.
        return {k: str(v) for k, v in raw.items() if v is not None}

    async def _rewrite_hash(self, overrides: dict[str, str]) -> bool:
        try:
            pipe = self._redis.pipeline(transaction=True)
            pipe.delete(AUTOSCALE_OVERRIDES_KEY)
            if overrides:
                pipe.hset(AUTOSCALE_OVERRIDES_KEY, mapping=overrides)
            await pipe.execute()
            return True
        except Exception:
            logger.warning("autoscale_overrides_rewrite_failed", exc_info=True)
            return False

    async def _run_loop(self) -> None:
        while self._running:
            await self.sync_once()
            await asyncio.sleep(RECONCILE_INTERVAL_S)
