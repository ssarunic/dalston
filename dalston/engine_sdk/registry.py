"""Batch engine registry client.

Handles engine registration, heartbeat, and unregistration to Redis.
Mirrors the realtime_sdk/registry.py pattern for batch engines.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

import redis
import structlog

from dalston.engine_sdk.types import EngineCapabilities

logger = structlog.get_logger()


# Redis key patterns (shared with orchestrator)
RUNTIME_SET_KEY = "dalston:batch:runtimes"  # Contains logical runtime names
INSTANCE_KEY_PREFIX = "dalston:batch:instance:"  # Hash key prefix for instance state
RUNTIME_INSTANCES_PREFIX = (
    "dalston:batch:runtime:instances:"  # Set of instances per runtime
)


@dataclass
class BatchEngineInfo:
    """Batch engine registration information.

    Attributes:
        runtime: The inference framework (e.g., "faster-whisper")
        instance: Unique instance identifier (e.g., "faster-whisper-a1b2c3d4e5f6")
        stage: Pipeline stage this engine handles (e.g., "transcribe")
        stream_name: Redis stream this engine polls (e.g., "dalston:stream:faster-whisper")
        capabilities: Engine capabilities for validation and routing
    """

    runtime: str
    instance: str
    stage: str
    stream_name: str
    capabilities: EngineCapabilities | None = None
    execution_profile: str = "container"


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
            runtime="faster-whisper",
            stage="transcribe",
            stream_name="dalston:stream:faster-whisper",
        ))

        # Send heartbeats periodically
        await registry.heartbeat(
            runtime="faster-whisper",
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
        Uses instance for Redis key to support spot instance replacement.

        Args:
            info: Engine registration information
        """
        r = self._get_redis()
        # Use instance for Redis key to ensure uniqueness across spot replacements
        instance_key = f"{INSTANCE_KEY_PREFIX}{info.instance}"
        now = datetime.now(UTC).isoformat()

        # Build mapping with required fields
        mapping: dict[str, str] = {
            "runtime": info.runtime,  # Inference framework for grouping/metrics
            "instance": info.instance,  # Unique instance identifier
            "stage": info.stage,
            "stream_name": info.stream_name,
            "execution_profile": info.execution_profile,
            "status": "idle",
            "current_task": "",
            "last_heartbeat": now,
            "registered_at": now,
        }

        # Include capabilities if provided
        if info.capabilities is not None:
            mapping["capabilities"] = info.capabilities.model_dump_json()

        # Set instance state
        r.hset(instance_key, mapping=mapping)

        # Set TTL on instance key
        r.expire(instance_key, self.HEARTBEAT_TTL)

        # Add runtime to main set (for scheduler availability checks)
        r.sadd(RUNTIME_SET_KEY, info.runtime)

        # Track this instance under the runtime
        instances_key = f"{RUNTIME_INSTANCES_PREFIX}{info.runtime}"
        r.sadd(instances_key, info.instance)

        # Store registration info for re-registration after TTL expiration
        self._registered_engines[info.instance] = info

        logger.info(
            "batch_engine_registered",
            runtime=info.runtime,
            instance=info.instance,
            stage=info.stage,
            stream_name=info.stream_name,
            has_capabilities=info.capabilities is not None,
        )

    def heartbeat(
        self,
        instance: str,
        status: str,
        current_task: str | None = None,
        capabilities: EngineCapabilities | None = None,
        loaded_model: str | None = None,
        local_cache: dict | None = None,
    ) -> None:
        """Send heartbeat update.

        Updates engine state and refreshes TTL. If the Redis key was deleted
        (e.g., TTL expired during system sleep), re-populates all registration
        fields from stored info.

        Args:
            instance: Unique instance identifier
            status: Engine status ("idle" or "processing")
            current_task: Current task ID if processing, None if idle
            capabilities: Engine capabilities (optional, included on first heartbeat)
            loaded_model: Currently loaded model ID for runtime model management (M36)
            local_cache: Local model cache stats from S3ModelStorage (M41)
                Example: {"models": ["model-a"], "total_size_mb": 3500, "model_count": 1}
        """
        r = self._get_redis()
        instance_key = f"{INSTANCE_KEY_PREFIX}{instance}"
        now = datetime.now(UTC).isoformat()

        # Check if key is missing or incomplete (TTL expired during sleep)
        existing_data = r.hget(instance_key, "runtime")
        needs_reregistration = existing_data is None

        if needs_reregistration and instance in self._registered_engines:
            # Re-populate all registration fields
            info = self._registered_engines[instance]
            mapping: dict[str, str] = {
                "runtime": info.runtime,
                "instance": info.instance,
                "stage": info.stage,
                "stream_name": info.stream_name,
                "execution_profile": info.execution_profile,
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

            # M41: Include local cache stats
            if local_cache is not None:
                mapping["local_cache"] = json.dumps(local_cache)

            # Re-add to sets (idempotent)
            r.sadd(RUNTIME_SET_KEY, info.runtime)
            instances_key = f"{RUNTIME_INSTANCES_PREFIX}{info.runtime}"
            r.sadd(instances_key, instance)

            logger.info(
                "batch_engine_reregistered",
                runtime=info.runtime,
                instance=instance,
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

            # M41: Include local cache stats
            if local_cache is not None:
                mapping["local_cache"] = json.dumps(local_cache)

        r.hset(instance_key, mapping=mapping)

        # Refresh TTL
        r.expire(instance_key, self.HEARTBEAT_TTL)

        logger.debug(
            "batch_engine_heartbeat",
            instance=instance,
            status=status,
            current_task=current_task,
            loaded_model=loaded_model,
            local_cache_models=local_cache.get("model_count") if local_cache else None,
        )

    def unregister(self, instance: str) -> None:
        """Unregister engine on shutdown.

        Removes engine from registry and cleans up related keys.

        Args:
            instance: Unique instance identifier
        """
        r = self._get_redis()
        instance_key = f"{INSTANCE_KEY_PREFIX}{instance}"

        # Get runtime from stored info to clean up instance set
        info = self._registered_engines.get(instance)
        if info:
            instances_key = f"{RUNTIME_INSTANCES_PREFIX}{info.runtime}"
            r.srem(instances_key, instance)

        # Delete instance state hash
        r.delete(instance_key)

        # Remove from local cache
        self._registered_engines.pop(instance, None)

        logger.info("batch_engine_unregistered", instance=instance)

    def close(self) -> None:
        """Close Redis connection."""
        if self._redis is not None:
            self._redis.close()
            self._redis = None
