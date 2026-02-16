"""Batch engine registry client.

Handles engine registration, heartbeat, and unregistration to Redis.
Mirrors the realtime_sdk/registry.py pattern for batch engines.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import redis
import structlog

from dalston.engine_sdk.types import EngineCapabilities

logger = structlog.get_logger()


# Redis key patterns (shared with orchestrator)
ENGINE_SET_KEY = "dalston:batch:engines"
ENGINE_KEY_PREFIX = "dalston:batch:engine:"


@dataclass
class BatchEngineInfo:
    """Batch engine registration information.

    Attributes:
        engine_id: Unique identifier for this engine (e.g., "faster-whisper")
        stage: Pipeline stage this engine handles (e.g., "transcribe")
        queue_name: Redis queue name this engine polls (e.g., "dalston:queue:faster-whisper")
        capabilities: Engine capabilities for validation and routing
    """

    engine_id: str
    stage: str
    queue_name: str
    capabilities: EngineCapabilities | None = None


class BatchEngineRegistry:
    """Client for registering batch engines with the orchestrator.

    Handles:
    - Engine registration on startup
    - Periodic heartbeat updates
    - Engine unregistration on shutdown

    Example:
        registry = BatchEngineRegistry("redis://localhost:6379")

        # Register on startup
        await registry.register(BatchEngineInfo(
            engine_id="faster-whisper",
            stage="transcribe",
            queue_name="dalston:queue:faster-whisper",
        ))

        # Send heartbeats periodically
        await registry.heartbeat(
            engine_id="faster-whisper",
            status="idle",
            current_task=None,
        )

        # Unregister on shutdown
        await registry.unregister("faster-whisper")
        await registry.close()
    """

    HEARTBEAT_TTL = 60  # seconds - engine considered offline if no heartbeat

    def __init__(self, redis_url: str) -> None:
        """Initialize registry client.

        Args:
            redis_url: Redis connection URL
        """
        self._redis_url = redis_url
        self._redis: redis.Redis | None = None

    def _get_redis(self) -> redis.Redis:
        """Get or create Redis connection (sync)."""
        if self._redis is None:
            self._redis = redis.from_url(
                self._redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
        return self._redis

    def register(self, info: BatchEngineInfo) -> None:
        """Register engine with the registry.

        Creates engine entry in Redis with initial state including capabilities.

        Args:
            info: Engine registration information
        """
        r = self._get_redis()
        engine_key = f"{ENGINE_KEY_PREFIX}{info.engine_id}"
        now = datetime.now(UTC).isoformat()

        # Build mapping with required fields
        mapping: dict[str, str] = {
            "engine_id": info.engine_id,
            "stage": info.stage,
            "queue_name": info.queue_name,
            "status": "idle",
            "current_task": "",
            "last_heartbeat": now,
            "registered_at": now,
        }

        # Include capabilities if provided
        if info.capabilities is not None:
            mapping["capabilities"] = info.capabilities.model_dump_json()

        # Set engine state
        r.hset(engine_key, mapping=mapping)

        # Set TTL on engine key
        r.expire(engine_key, self.HEARTBEAT_TTL)

        # Add to engine set
        r.sadd(ENGINE_SET_KEY, info.engine_id)

        logger.info(
            "batch_engine_registered",
            engine_id=info.engine_id,
            stage=info.stage,
            queue_name=info.queue_name,
            has_capabilities=info.capabilities is not None,
        )

    def heartbeat(
        self,
        engine_id: str,
        status: str,
        current_task: str | None = None,
        capabilities: EngineCapabilities | None = None,
    ) -> None:
        """Send heartbeat update.

        Updates engine state and refreshes TTL. Optionally updates capabilities.

        Args:
            engine_id: Engine identifier
            status: Engine status ("idle" or "processing")
            current_task: Current task ID if processing, None if idle
            capabilities: Engine capabilities (optional, included on first heartbeat)
        """
        r = self._get_redis()
        engine_key = f"{ENGINE_KEY_PREFIX}{engine_id}"

        mapping: dict[str, str] = {
            "status": status,
            "current_task": current_task or "",
            "last_heartbeat": datetime.now(UTC).isoformat(),
        }

        # Include capabilities if provided
        if capabilities is not None:
            mapping["capabilities"] = capabilities.model_dump_json()

        r.hset(engine_key, mapping=mapping)

        # Refresh TTL
        r.expire(engine_key, self.HEARTBEAT_TTL)

        logger.debug(
            "batch_engine_heartbeat",
            engine_id=engine_id,
            status=status,
            current_task=current_task,
        )

    def unregister(self, engine_id: str) -> None:
        """Unregister engine on shutdown.

        Removes engine from registry and cleans up related keys.

        Args:
            engine_id: Engine identifier
        """
        r = self._get_redis()
        engine_key = f"{ENGINE_KEY_PREFIX}{engine_id}"

        # Remove from engine set
        r.srem(ENGINE_SET_KEY, engine_id)

        # Delete engine state
        r.delete(engine_key)

        logger.info("batch_engine_unregistered", engine_id=engine_id)

    def close(self) -> None:
        """Close Redis connection."""
        if self._redis is not None:
            self._redis.close()
            self._redis = None
