"""Server-side batch engine registry for orchestrator.

Reads engine state from Redis (written by engine_sdk's BatchEngineRegistry client).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import redis.asyncio as redis
import structlog

from dalston.engine_sdk.types import EngineCapabilities

logger = structlog.get_logger()


# Redis key patterns (shared with engine_sdk)
ENGINE_SET_KEY = "dalston:batch:runtimes"  # Contains logical runtimes
ENGINE_KEY_PREFIX = "dalston:batch:instance:"  # Hash key prefix for instance state
ENGINE_INSTANCES_PREFIX = (
    "dalston:batch:runtime:instances:"  # Set of instances per runtime
)

# Heartbeat timeout - engine considered offline if no heartbeat within this period
HEARTBEAT_TIMEOUT_SECONDS = 60


@dataclass
class BatchEngineState:
    """Batch engine state read from Redis.

    Attributes:
        runtime: Logical identifier for grouping (e.g., "faster-whisper")
        instance: Unique instance identifier (e.g., "faster-whisper-abc123")
        stage: Pipeline stage this engine handles (e.g., "transcribe")
        stream_name: Redis stream this engine polls
        status: Current status ("idle", "processing", "offline")
        current_task: Task ID currently being processed, or None
        last_heartbeat: Last heartbeat timestamp
        registered_at: Engine registration timestamp
        capabilities: Engine capabilities for validation (M29)
        loaded_model: Currently loaded model ID for runtime model management (M36)
    """

    runtime: str
    instance: str
    stage: str
    stream_name: str
    status: str
    current_task: str | None
    last_heartbeat: datetime
    registered_at: datetime
    capabilities: EngineCapabilities | None = None
    loaded_model: str | None = None

    @property
    def is_available(self) -> bool:
        """Whether engine is available for task routing.

        An engine is available if:
        - Status is not "offline"
        - Last heartbeat is within the timeout period
        """
        if self.status == "offline":
            return False
        age = (datetime.now(UTC) - self.last_heartbeat).total_seconds()
        return age < HEARTBEAT_TIMEOUT_SECONDS

    def supports_language(self, language: str) -> bool:
        """Check if engine supports a specific language.

        Args:
            language: ISO 639-1 language code (e.g., "en", "hr")

        Returns:
            True if supported (or capabilities not declared, for backward compat)
        """
        if self.capabilities is None:
            return True  # Backward compatibility with M28 engines
        if self.capabilities.languages is None:
            return True  # None means all languages
        return language.lower() in [
            lang.lower() for lang in self.capabilities.languages
        ]


class BatchEngineRegistry:
    """Server-side registry for reading batch engine pool state.

    Used by orchestrator to:
    - List all registered engines
    - Check if specific engines are available before queuing tasks
    - Get engine state for monitoring

    Example:
        registry = BatchEngineRegistry(redis_client)

        # Check before queuing task
        if not await registry.is_engine_available("faster-whisper"):
            raise EngineUnavailableError(...)

        # Get all engines for status endpoint
        engines = await registry.get_engines()

        # Get engines for specific stage
        transcribers = await registry.get_engines_for_stage("transcribe")
    """

    def __init__(self, redis_client: redis.Redis) -> None:
        """Initialize registry with Redis client.

        Args:
            redis_client: Async Redis client
        """
        self._redis = redis_client

    async def get_engines(self) -> list[BatchEngineState]:
        """Get all registered engines.

        Returns all instances across all logical runtime types.

        Returns:
            List of all engine instance states
        """
        runtimes = await self._redis.smembers(ENGINE_SET_KEY)
        engines = []

        for runtime in runtimes:
            instances = await self.get_engine_instances(runtime)
            engines.extend(instances)

        return engines

    async def get_engine(self, runtime: str) -> BatchEngineState | None:
        """Get specific engine state (first available instance).

        Queries the per-runtime instance set and returns the first available
        instance, or any instance if none are available.

        Args:
            runtime: Logical runtime identifier (e.g., "faster-whisper")

        Returns:
            BatchEngineState of first available instance, or None if no instances
        """
        instances = await self.get_engine_instances(runtime)
        if not instances:
            return None

        # Return first available instance, or first instance if none available
        for inst in instances:
            if inst.is_available:
                return inst
        return instances[0]

    async def get_engine_instances(self, runtime: str) -> list[BatchEngineState]:
        """Get all instances for a logical runtime type.

        Args:
            runtime: Logical runtime identifier (e.g., "faster-whisper")

        Returns:
            List of all instance states for this runtime type
        """
        instances_key = f"{ENGINE_INSTANCES_PREFIX}{runtime}"
        instance_ids = await self._redis.smembers(instances_key)
        instances = []

        for instance_id in instance_ids:
            inst = await self.get_engine_instance(instance_id)
            if inst is not None:
                instances.append(inst)

        return instances

    async def get_engine_instance(self, instance: str) -> BatchEngineState | None:
        """Get specific engine instance state by instance ID.

        Args:
            instance: Instance-unique identifier (e.g., "faster-whisper-abc123")

        Returns:
            BatchEngineState if found, None otherwise
        """
        instance_key = f"{ENGINE_KEY_PREFIX}{instance}"
        data = await self._redis.hgetall(instance_key)

        if not data:
            return None

        return self._parse_engine_state(instance, data)

    async def get_engines_for_stage(self, stage: str) -> list[BatchEngineState]:
        """Get all engines that handle a specific pipeline stage.

        Args:
            stage: Pipeline stage (e.g., "transcribe", "align", "diarize")

        Returns:
            List of engines for the given stage
        """
        engines = await self.get_engines()
        return [e for e in engines if e.stage == stage]

    async def is_engine_available(self, runtime: str) -> bool:
        """Check if a runtime has at least one healthy instance.

        Args:
            runtime: Logical runtime identifier (e.g., "faster-whisper")

        Returns:
            True if at least one instance is available for task routing
        """
        instances = await self.get_engine_instances(runtime)
        return any(inst.is_available for inst in instances)

    async def mark_instance_offline(self, instance: str) -> None:
        """Mark engine instance as offline due to stale heartbeat.

        Args:
            instance: Instance-unique identifier
        """
        instance_key = f"{ENGINE_KEY_PREFIX}{instance}"
        await self._redis.hset(instance_key, "status", "offline")
        logger.warning("batch_engine_instance_marked_offline", instance=instance)

    async def mark_engine_offline(self, runtime: str) -> None:
        """Mark all instances of a runtime as offline.

        Args:
            runtime: Logical runtime identifier
        """
        instances = await self.get_engine_instances(runtime)
        for inst in instances:
            await self.mark_instance_offline(inst.instance)

    def _parse_engine_state(self, instance: str, data: dict) -> BatchEngineState:
        """Parse engine state from Redis hash data.

        Args:
            instance: Instance-unique identifier used as Redis key
            data: Hash data from Redis
        """
        current_task = data.get("current_task", "")

        # Parse capabilities JSON if present
        capabilities = None
        capabilities_json = data.get("capabilities")
        if capabilities_json:
            try:
                capabilities = EngineCapabilities.model_validate_json(capabilities_json)
            except Exception:
                logger.warning(
                    "failed_to_parse_capabilities",
                    instance=instance,
                    capabilities_json=capabilities_json[:100]
                    if capabilities_json
                    else None,
                )

        # Use instance from data if available, fallback to key
        actual_instance = data.get("instance", instance)
        # Use runtime from data if available, fallback to instance
        runtime = data.get("runtime", instance)

        # M36: Get loaded model for runtime model management
        loaded_model = data.get("loaded_model")

        return BatchEngineState(
            runtime=runtime,
            instance=actual_instance,
            stage=data.get("stage", "unknown"),
            # Prefer stream_name, fall back to queue_name for backward compat
            stream_name=data.get("stream_name") or data.get("queue_name", ""),
            status=data.get("status", "offline"),
            current_task=current_task if current_task else None,
            last_heartbeat=self._parse_datetime(data.get("last_heartbeat")),
            registered_at=self._parse_datetime(data.get("registered_at")),
            capabilities=capabilities,
            loaded_model=loaded_model if loaded_model else None,
        )

    def _parse_datetime(self, value: str | None) -> datetime:
        """Parse ISO datetime string."""
        if value:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                pass
        return datetime.now(UTC)
