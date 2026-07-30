"""External-inheritance sources for settings namespaces (M95).

Some namespaces' effective values live outside Postgres — the autoscale
knobs are owned by the control-plane policy YAML, observable only through
the tick snapshots the autoscaler publishes to Redis. This module gives the
console layer a small protocol for enriching ResolvedSettings with those
externally-known effective values, keeping SettingsService itself
Redis-free and namespace-agnostic.

Adding another externally-inherited namespace = implement the protocol,
add one EXTERNAL_INHERITANCE_SOURCES entry.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog
from redis.asyncio import Redis

from dalston.common.registry import (
    AUTOSCALE_SHAPES_KEY,
    AUTOSCALE_TICK_KEY_PREFIX,
    AUTOSCALE_TICK_SCHEMA_VERSION,
    is_autoscale_tick_stale,
)

logger = structlog.get_logger()


@dataclass(frozen=True)
class ExternalEffectiveValue:
    """One key's externally-known effective value.

    known=False means the source cannot currently answer (stale or missing
    snapshot) — render "unknown", never a guessed number.
    """

    value: Any
    known: bool
    override_error: str | None = None


class ExternalInheritanceSource(Protocol):
    """Read-side protocol: where do a namespace's unset values inherit from?"""

    async def is_active(self, redis: Redis) -> bool:
        """Whether this source exists on this deployment at all (drives
        namespace visibility — hidden when inactive)."""
        ...

    async def effective_values(
        self, redis: Redis, keys: list[str]
    ) -> dict[str, ExternalEffectiveValue]:
        """Effective value per key, as the external consumer reports them."""
        ...


class AutoscaleInheritanceSource:
    """Reads effective autoscale policy from the latest tick snapshot.

    Uses the alphabetically-first configured shape as representative: the
    overrides hash is global (per-shape settings are an explicit M95
    non-goal), so any shape's echo reflects the same override layer.
    """

    async def is_active(self, redis: Redis) -> bool:
        try:
            return bool(await redis.hlen(AUTOSCALE_SHAPES_KEY))
        except Exception:
            logger.warning("autoscale_shapes_marker_unreadable", exc_info=True)
            return False

    async def effective_values(
        self, redis: Redis, keys: list[str]
    ) -> dict[str, ExternalEffectiveValue]:
        policy = await self._representative_policy_echo(redis)
        if policy is None:
            return {k: ExternalEffectiveValue(value=None, known=False) for k in keys}
        override_error = policy.get("override_error")
        return {
            k: ExternalEffectiveValue(
                value=policy.get(k),
                known=k in policy,
                override_error=override_error,
            )
            for k in keys
        }

    async def _representative_policy_echo(self, redis: Redis) -> dict | None:
        """Policy echo from the first shape with a fresh, parseable
        snapshot; None when the autoscaler isn't reporting."""
        try:
            marker = await redis.hgetall(AUTOSCALE_SHAPES_KEY)
            if not isinstance(marker, dict) or not marker:
                return None
            now = datetime.now(UTC)
            for shape in sorted(marker):
                raw = await redis.get(f"{AUTOSCALE_TICK_KEY_PREFIX}{shape}")
                if raw is None:
                    continue
                try:
                    snap = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                if snap.get("schema_version") != AUTOSCALE_TICK_SCHEMA_VERSION:
                    continue
                if is_autoscale_tick_stale(snap.get("ts"), now):
                    continue
                policy = snap.get("policy")
                if isinstance(policy, dict):
                    return policy
        except Exception:
            logger.warning("autoscale_policy_echo_unreadable", exc_info=True)
        return None


EXTERNAL_INHERITANCE_SOURCES: dict[str, ExternalInheritanceSource] = {
    "autoscale": AutoscaleInheritanceSource(),
}
