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
ENGINE_SET_KEY = "dalston:batch:engines"  # Contains logical engine_ids
ENGINE_KEY_PREFIX = "dalston:batch:engine:"  # Hash key prefix for instance state
ENGINE_INSTANCES_PREFIX = (
    "dalston:batch:engine:instances:"  # Set of instances per engine
)


@dataclass
class BatchEngineInfo:
    """Batch engine registration information.

    Attributes:
        engine_id: Logical identifier for this engine type (e.g., "faster-whisper")
        instance_id: Instance-unique identifier (e.g., "faster-whisper-a1b2c3d4e5f6")
        stage: Pipeline stage this engine handles (e.g., "transcribe")
        stream_name: Redis stream this engine polls (e.g., "dalston:stream:faster-whisper")
        capabilities: Engine capabilities for validation and routing
    """

    engine_id: str
    instance_id: str
    stage: str
    stream_name: str
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
            stream_name="dalston:stream:faster-whisper",
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
        # Store registration info for re-registration after TTL expiration
        self._registered_engines: dict[str, BatchEngineInfo] = {}

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
        Uses instance_id for Redis key to support spot instance replacement.

        Args:
            info: Engine registration information
        """
        r = self._get_redis()
        # Use instance_id for Redis key to ensure uniqueness across spot replacements
        engine_key = f"{ENGINE_KEY_PREFIX}{info.instance_id}"
        now = datetime.now(UTC).isoformat()

        # Build mapping with required fields
        mapping: dict[str, str] = {
            "engine_id": info.engine_id,  # Logical ID for grouping/metrics
            "instance_id": info.instance_id,  # Unique instance identifier
            "stage": info.stage,
            "stream_name": info.stream_name,
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

        # Add logical engine_id to main set (for scheduler availability checks)
        r.sadd(ENGINE_SET_KEY, info.engine_id)

        # Track this instance under the logical engine
        instances_key = f"{ENGINE_INSTANCES_PREFIX}{info.engine_id}"
        r.sadd(instances_key, info.instance_id)

        # Store registration info for re-registration after TTL expiration
        self._registered_engines[info.instance_id] = info

        logger.info(
            "batch_engine_registered",
            engine_id=info.engine_id,
            instance_id=info.instance_id,
            stage=info.stage,
            stream_name=info.stream_name,
            has_capabilities=info.capabilities is not None,
        )

    def heartbeat(
        self,
        instance_id: str,
        status: str,
        current_task: str | None = None,
        capabilities: EngineCapabilities | None = None,
        loaded_model: str | None = None,
    ) -> None:
        """Send heartbeat update.

        Updates engine state and refreshes TTL. If the Redis key was deleted
        (e.g., TTL expired during system sleep), re-populates all registration
        fields from stored info.

        Args:
            instance_id: Instance-unique identifier
            status: Engine status ("idle" or "processing")
            current_task: Current task ID if processing, None if idle
            capabilities: Engine capabilities (optional, included on first heartbeat)
            loaded_model: Currently loaded model ID for runtime model management (M36)
        """
        r = self._get_redis()
        engine_key = f"{ENGINE_KEY_PREFIX}{instance_id}"
        now = datetime.now(UTC).isoformat()

        # Check if key is missing or incomplete (TTL expired during sleep)
        existing_data = r.hget(engine_key, "engine_id")
        needs_reregistration = existing_data is None

        if needs_reregistration and instance_id in self._registered_engines:
            # Re-populate all registration fields
            info = self._registered_engines[instance_id]
            mapping: dict[str, str] = {
                "engine_id": info.engine_id,
                "instance_id": info.instance_id,
                "stage": info.stage,
                "stream_name": info.stream_name,
                "status": status,
                "current_task": current_task or "",
                "last_heartbeat": now,
                "registered_at": now,
            }

            # Use stored capabilities if not provided in this call
            caps = capabilities or info.capabilities
            if caps is not None:
                mapping["capabilities"] = caps.model_dump_json()

            # M36: Include loaded model for runtime model management
            if loaded_model is not None:
                mapping["loaded_model"] = loaded_model

            # Re-add to sets (idempotent)
            r.sadd(ENGINE_SET_KEY, info.engine_id)
            instances_key = f"{ENGINE_INSTANCES_PREFIX}{info.engine_id}"
            r.sadd(instances_key, instance_id)

            logger.info(
                "batch_engine_reregistered",
                engine_id=info.engine_id,
                instance_id=instance_id,
                stage=info.stage,
                reason="ttl_expired",
            )
        else:
            # Normal heartbeat - just update dynamic fields
            mapping = {
                "status": status,
                "current_task": current_task or "",
                "last_heartbeat": now,
            }

            # Include capabilities if provided
            if capabilities is not None:
                mapping["capabilities"] = capabilities.model_dump_json()

            # M36: Include loaded model for runtime model management
            if loaded_model is not None:
                mapping["loaded_model"] = loaded_model

        r.hset(engine_key, mapping=mapping)

        # Refresh TTL
        r.expire(engine_key, self.HEARTBEAT_TTL)

        logger.debug(
            "batch_engine_heartbeat",
            instance_id=instance_id,
            status=status,
            current_task=current_task,
            loaded_model=loaded_model,
        )

    def unregister(self, instance_id: str) -> None:
        """Unregister engine on shutdown.

        Removes engine from registry and cleans up related keys.

        Args:
            instance_id: Instance-unique identifier
        """
        r = self._get_redis()
        engine_key = f"{ENGINE_KEY_PREFIX}{instance_id}"

        # Get engine_id from stored info to clean up instance set
        info = self._registered_engines.get(instance_id)
        if info:
            instances_key = f"{ENGINE_INSTANCES_PREFIX}{info.engine_id}"
            r.srem(instances_key, instance_id)

        # Delete engine state hash
        r.delete(engine_key)

        # Remove from local cache
        self._registered_engines.pop(instance_id, None)

        logger.info("batch_engine_unregistered", instance_id=instance_id)

    def close(self) -> None:
        """Close Redis connection."""
        if self._redis is not None:
            self._redis.close()
            self._redis = None
