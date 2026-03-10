"""Server-side batch engine registry for orchestrator.

Reads engine state from Redis (written by engine_sdk's BatchEngineRegistry client).

M64: When DALSTON_REGISTRY_UNIFIED_READ_ENABLED=true, reads from the unified
registry first with legacy fallback. The unified registry is populated by
dual-write from engine runners.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import redis.asyncio as redis
import structlog

from dalston.common.registry import EngineRecord, UnifiedEngineRegistry
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
    execution_profile: str = "container"

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

    def __init__(
        self,
        redis_client: redis.Redis,
        *,
        unified_read_enabled: bool | None = None,
    ) -> None:
        """Initialize registry with Redis client.

        Args:
            redis_client: Async Redis client
            unified_read_enabled: Read from unified registry first (M64).
                If None, reads from DALSTON_REGISTRY_UNIFIED_READ_ENABLED env var.
        """
        self._redis = redis_client

        # M64: Optionally read from unified registry
        if unified_read_enabled is None:
            import os

            unified_read_enabled = (
                os.environ.get("DALSTON_REGISTRY_UNIFIED_READ_ENABLED", "false").lower()
                == "true"
            )
        self._unified_read_enabled = unified_read_enabled
        self._unified = (
            UnifiedEngineRegistry(redis_client) if unified_read_enabled else None
        )

    async def get_engines(self) -> list[BatchEngineState]:
        """Get all registered engines.

        Returns all instances across all logical runtime types.

        Returns:
            List of all engine instance states
        """
        # M64: Try unified registry first (filter to batch-interface engines)
        if self._unified:
            try:
                records = await self._unified.get_all()
                batch_records = [r for r in records if r.supports_interface("batch")]
                if batch_records:
                    return [_engine_record_to_state(r) for r in batch_records]
            except Exception:
                logger.debug("unified_registry_read_fallback", method="get_engines")

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
        # M64: Try unified registry first (batch-interface only)
        if self._unified:
            try:
                records = await self._unified.get_by_runtime(runtime)
                batch_records = [r for r in records if r.supports_interface("batch")]
                if batch_records:
                    # Return first available, or first if none available
                    for r in batch_records:
                        if r.is_available:
                            return _engine_record_to_state(r)
                    return _engine_record_to_state(batch_records[0])
            except Exception:
                logger.debug("unified_registry_read_fallback", method="get_engine")

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
        # M64: Try unified registry first (has stage index, filter to batch)
        if self._unified:
            try:
                records = await self._unified.get_by_stage(stage)
                batch_records = [r for r in records if r.supports_interface("batch")]
                if batch_records:
                    return [_engine_record_to_state(r) for r in batch_records]
            except Exception:
                logger.debug(
                    "unified_registry_read_fallback",
                    method="get_engines_for_stage",
                )

        engines = await self.get_engines()
        return [e for e in engines if e.stage == stage]

    async def is_engine_available(self, runtime: str) -> bool:
        """Check if a runtime has at least one healthy instance.

        Args:
            runtime: Logical runtime identifier (e.g., "faster-whisper")

        Returns:
            True if at least one instance is available for task routing
        """
        # M64: Try unified registry first (batch-interface only)
        if self._unified:
            try:
                records = await self._unified.get_available(
                    runtime=runtime, interface="batch"
                )
                if records:
                    return True
                # Empty result: fall through to legacy (batch engines may
                # not be in unified registry yet during dual migration)
            except Exception:
                logger.debug(
                    "unified_registry_read_fallback",
                    method="is_engine_available",
                )

        instances = await self.get_engine_instances(runtime)
        return any(inst.is_available for inst in instances)

    async def mark_instance_offline(self, instance: str) -> None:
        """Mark engine instance as offline due to stale heartbeat.

        Args:
            instance: Instance-unique identifier
        """
        instance_key = f"{ENGINE_KEY_PREFIX}{instance}"
        await self._redis.hset(instance_key, "status", "offline")

        # M64: Also mark offline in unified registry
        if self._unified:
            try:
                await self._unified.mark_instance_offline(instance)
            except Exception:
                logger.debug("unified_registry_mark_offline_failed", instance=instance)

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
            execution_profile=data.get("execution_profile", "container"),
        )

    def _parse_datetime(self, value: str | None) -> datetime:
        """Parse ISO datetime string."""
        if value:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                pass
        return datetime.now(UTC)


def _engine_record_to_state(record: EngineRecord) -> BatchEngineState:
    """Convert unified EngineRecord to BatchEngineState for backward compat.

    M64: This bridge allows consumers to read from the unified registry
    while keeping their existing interfaces unchanged.
    """
    return BatchEngineState(
        runtime=record.runtime,
        instance=record.instance,
        stage=record.stage,
        stream_name=record.stream_name or "",
        status=record.status,
        current_task=None,
        last_heartbeat=record.last_heartbeat or datetime.now(UTC),
        registered_at=record.registered_at or datetime.now(UTC),
        capabilities=record.capabilities,
        loaded_model=record.loaded_model,
        execution_profile=record.execution_profile,
    )
