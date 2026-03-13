"""Unified engine registry for batch and real-time engines (M64/M69).

Replaces the split batch/RT registry surfaces with one unified model.
Migration scaffolding (DALSTON_ENGINE_REGISTRY_MODE) removed in M69.

Redis key schema:
    dalston:engine:instances              SET of all instance IDs
    dalston:engine:instance:{id}          HASH with EngineRecord fields
    dalston:engine:engine_id:{engine_id}      SET of instance IDs per engine_id
    dalston:engine:stage:{stage}          SET of instance IDs per stage
    dalston:engine:events                 PUB/SUB channel for lifecycle events

TTL: Instance hashes expire after HEARTBEAT_TTL seconds (60s default).
Heartbeats refresh TTL. Missing key triggers re-registration from stored info.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import redis.asyncio as aioredis
import structlog

if TYPE_CHECKING:
    # EngineCapabilities is only needed for type annotations and for parsing in
    # _build_engine_record().  Importing engine_sdk.types at module level would
    # trigger engine_sdk/__init__.py → runner.py → registry.py (circular).
    from dalston.engine_sdk.types import EngineCapabilities

logger = structlog.get_logger()


# Redis key patterns
UNIFIED_INSTANCE_SET_KEY = "dalston:engine:instances"
UNIFIED_INSTANCE_KEY_PREFIX = "dalston:engine:instance:"
UNIFIED_RUNTIME_SET_PREFIX = "dalston:engine:engine_id:"
UNIFIED_STAGE_SET_PREFIX = "dalston:engine:stage:"
UNIFIED_EVENTS_CHANNEL = "dalston:engine:events"

# Heartbeat configuration
HEARTBEAT_TTL = 60  # seconds
HEARTBEAT_TIMEOUT_SECONDS = 60  # consumer-side staleness threshold


@dataclass
class EngineRecord:
    """Unified engine registration record.

    Represents batch-only, RT-only, or dual-interface engines in a single
    schema. Both batch and realtime registration flows produce this record.

    Attributes:
        instance: Unique instance identifier (e.g., "faster-whisper-a1b2c3d4")
        engine_id: Inference framework (e.g., "faster-whisper", "nemo")
        stage: Pipeline stage (e.g., "transcribe", "diarize")
        status: Current status (idle, processing, ready, busy, draining, offline)
        interfaces: Supported I/O modes (["batch"], ["realtime"], ["batch", "realtime"])
        capacity: Max concurrent work items (sessions for RT, inflight tasks for batch)
        active_batch: Number of active batch tasks
        active_realtime: Number of active realtime sessions
        models_loaded: List of currently loaded model IDs
        includes_diarization: Whether engine includes speaker labels
        endpoint: WebSocket endpoint URL (RT engines only)
        stream_name: Redis stream key (batch engines only)
        gpu_memory_used: GPU memory usage string
        gpu_memory_total: Total GPU memory string
        last_heartbeat: Last heartbeat timestamp
        registered_at: Registration timestamp
        capabilities: Full engine capabilities from engine.yaml
        loaded_model: Currently loaded model ID (batch engines, M36)
        execution_profile: Execution profile (container, lite, etc.)
    """

    instance: str
    engine_id: str
    stage: str
    status: str
    interfaces: list[str]
    capacity: int = 1
    active_batch: int = 0
    active_realtime: int = 0
    models_loaded: list[str] | None = None
    includes_diarization: bool = False
    endpoint: str | None = None
    stream_name: str | None = None
    gpu_memory_used: str = "0GB"
    gpu_memory_total: str = "0GB"
    last_heartbeat: datetime | None = None
    registered_at: datetime | None = None
    capabilities: EngineCapabilities | None = None
    loaded_model: str | None = None
    execution_profile: str = "container"

    @property
    def available_capacity(self) -> int:
        """Remaining capacity for new work."""
        return max(0, self.capacity - self.active_batch - self.active_realtime)

    @property
    def is_available(self) -> bool:
        """Whether engine can accept new work.

        Available if:
        - Status is not "offline" or "draining"
        - Last heartbeat is within timeout
        - Has remaining capacity
        """
        if self.status in ("offline", "draining"):
            return False
        if self.last_heartbeat is not None:
            age = (datetime.now(UTC) - self.last_heartbeat).total_seconds()
            if age >= HEARTBEAT_TIMEOUT_SECONDS:
                return False
        return self.available_capacity > 0

    @property
    def is_healthy(self) -> bool:
        """Whether engine has a fresh heartbeat (regardless of capacity)."""
        if self.status == "offline":
            return False
        if self.last_heartbeat is not None:
            age = (datetime.now(UTC) - self.last_heartbeat).total_seconds()
            return age < HEARTBEAT_TIMEOUT_SECONDS
        return False

    def supports_interface(self, interface: str) -> bool:
        """Check if engine supports a specific interface (batch/realtime)."""
        return interface in self.interfaces


def _record_to_mapping(record: EngineRecord) -> dict[str, str]:
    """Serialize EngineRecord to a flat dict for Redis HSET."""
    now = datetime.now(UTC).isoformat()
    mapping: dict[str, str] = {
        "instance": record.instance,
        "engine_id": record.engine_id,
        "stage": record.stage,
        "status": record.status,
        "interfaces": json.dumps(record.interfaces),
        "capacity": str(record.capacity),
        "active_batch": str(record.active_batch),
        "active_realtime": str(record.active_realtime),
        "gpu_memory_used": record.gpu_memory_used,
        "gpu_memory_total": record.gpu_memory_total,
        "last_heartbeat": (
            record.last_heartbeat.isoformat() if record.last_heartbeat else now
        ),
        "registered_at": (
            record.registered_at.isoformat() if record.registered_at else now
        ),
        "includes_diarization": "true" if record.includes_diarization else "false",
        "execution_profile": record.execution_profile,
    }

    if record.models_loaded is not None:
        mapping["models_loaded"] = json.dumps(record.models_loaded)
    if record.endpoint is not None:
        mapping["endpoint"] = record.endpoint
    if record.stream_name is not None:
        mapping["stream_name"] = record.stream_name
    if record.capabilities is not None:
        mapping["capabilities"] = record.capabilities.model_dump_json()
    if record.loaded_model is not None:
        mapping["loaded_model"] = record.loaded_model

    return mapping


def _mapping_to_record(instance: str, data: dict[str, str]) -> EngineRecord | None:
    """Deserialize Redis hash data to EngineRecord.

    Returns None if critical fields are missing (quarantines the instance).
    """
    engine_id = data.get("engine_id")
    if not engine_id:
        logger.error("unified_registry_missing_engine_id", instance=instance)
        return None

    stage = data.get("stage", "unknown")

    # Parse interfaces
    try:
        interfaces = json.loads(data.get("interfaces", '["batch"]'))
        if not isinstance(interfaces, list):
            interfaces = ["batch"]
    except (json.JSONDecodeError, ValueError):
        interfaces = ["batch"]

    # Parse numeric fields
    try:
        capacity = int(data.get("capacity", "1"))
    except (ValueError, TypeError):
        capacity = 1

    try:
        active_batch = int(data.get("active_batch", "0"))
    except (ValueError, TypeError):
        active_batch = 0

    try:
        active_realtime = int(data.get("active_realtime", "0"))
    except (ValueError, TypeError):
        active_realtime = 0

    # Parse JSON list fields
    models_loaded = _parse_json_list(data.get("models_loaded"))

    # Parse capabilities
    capabilities = None
    caps_json = data.get("capabilities")
    if caps_json:
        try:
            from dalston.engine_sdk.types import (
                EngineCapabilities,  # local import avoids circular dep at module level
            )

            capabilities = EngineCapabilities.model_validate_json(caps_json)
        except Exception:
            logger.warning(
                "unified_registry_invalid_capabilities",
                instance=instance,
            )

    return EngineRecord(
        instance=data.get("instance", instance),
        engine_id=engine_id,
        stage=stage,
        status=data.get("status", "offline"),
        interfaces=interfaces,
        capacity=capacity,
        active_batch=active_batch,
        active_realtime=active_realtime,
        models_loaded=models_loaded,
        includes_diarization=data.get("includes_diarization") == "true",
        endpoint=data.get("endpoint"),
        stream_name=data.get("stream_name"),
        gpu_memory_used=data.get("gpu_memory_used", "0GB"),
        gpu_memory_total=data.get("gpu_memory_total", "0GB"),
        last_heartbeat=_parse_datetime(data.get("last_heartbeat")),
        registered_at=_parse_datetime(data.get("registered_at")),
        capabilities=capabilities,
        loaded_model=data.get("loaded_model") or None,
        execution_profile=data.get("execution_profile", "container"),
    )


def _parse_json_list(value: str | None) -> list[str] | None:
    """Parse a JSON list string, returning None if not present."""
    if value is None:
        return None
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass
    return None


def _parse_datetime(value: str | None) -> datetime | None:
    """Parse ISO datetime string."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


class UnifiedEngineRegistry:
    """Async server-side registry for reading unified engine state.

    Used by orchestrator, gateway, and session router to discover engines.
    Reads from the unified Redis key namespace (dalston:engine:*).

    Example:
        registry = UnifiedEngineRegistry(redis_client)

        # Get all engines
        engines = await registry.get_all()

        # Get engines for a stage
        transcribers = await registry.get_by_stage("transcribe")

        # Get available RT workers
        rt_workers = await registry.get_available(
            interface="realtime"
        )
    """

    def __init__(self, redis_client: aioredis.Redis) -> None:  # type: ignore[type-arg]
        self._redis: Any = redis_client

    async def register(self, record: EngineRecord) -> None:
        """Write engine record to unified registry."""
        instance_key = f"{UNIFIED_INSTANCE_KEY_PREFIX}{record.instance}"
        mapping = _record_to_mapping(record)

        await self._redis.hset(instance_key, mapping=mapping)
        await self._redis.expire(instance_key, HEARTBEAT_TTL)

        # Index by instance set, engine_id, and stage
        await self._redis.sadd(UNIFIED_INSTANCE_SET_KEY, record.instance)
        await self._redis.sadd(
            f"{UNIFIED_RUNTIME_SET_PREFIX}{record.engine_id}", record.instance
        )
        await self._redis.sadd(
            f"{UNIFIED_STAGE_SET_PREFIX}{record.stage}", record.instance
        )

        logger.debug(
            "unified_registry_registered",
            instance=record.instance,
            engine_id=record.engine_id,
            interfaces=record.interfaces,
        )

    async def heartbeat(
        self,
        instance: str,
        *,
        status: str | None = None,
        active_batch: int | None = None,
        active_realtime: int | None = None,
        loaded_model: str | None = None,
        models_loaded: list[str] | None = None,
        gpu_memory_used: str | None = None,
    ) -> None:
        """Update heartbeat and dynamic fields.

        Only updates fields that are provided (not None).
        """
        instance_key = f"{UNIFIED_INSTANCE_KEY_PREFIX}{instance}"
        now = datetime.now(UTC).isoformat()

        mapping: dict[str, str] = {"last_heartbeat": now}

        if status is not None:
            mapping["status"] = status
        if active_batch is not None:
            mapping["active_batch"] = str(active_batch)
        if active_realtime is not None:
            mapping["active_realtime"] = str(active_realtime)
        if loaded_model is not None:
            mapping["loaded_model"] = loaded_model
        if models_loaded is not None:
            mapping["models_loaded"] = json.dumps(models_loaded)
        if gpu_memory_used is not None:
            mapping["gpu_memory_used"] = gpu_memory_used

        await self._redis.hset(instance_key, mapping=mapping)
        await self._redis.expire(instance_key, HEARTBEAT_TTL)

    async def deregister(self, instance: str) -> None:
        """Remove engine from unified registry."""
        instance_key = f"{UNIFIED_INSTANCE_KEY_PREFIX}{instance}"

        # Read engine_id and stage before deletion for index cleanup
        data = await self._redis.hmget(instance_key, "engine_id", "stage")
        engine_id = data[0]
        stage = data[1]

        await self._redis.delete(instance_key)
        await self._redis.srem(UNIFIED_INSTANCE_SET_KEY, instance)

        if engine_id:
            await self._redis.srem(f"{UNIFIED_RUNTIME_SET_PREFIX}{engine_id}", instance)
        if stage:
            await self._redis.srem(f"{UNIFIED_STAGE_SET_PREFIX}{stage}", instance)

        logger.debug("unified_registry_deregistered", instance=instance)

    async def get_by_instance(self, instance: str) -> EngineRecord | None:
        """Get a single engine record by instance ID."""
        instance_key = f"{UNIFIED_INSTANCE_KEY_PREFIX}{instance}"
        data = await self._redis.hgetall(instance_key)
        if not data:
            return None
        return _mapping_to_record(instance, data)

    async def get_all(self) -> list[EngineRecord]:
        """Get all registered engines."""
        instances = await self._redis.smembers(UNIFIED_INSTANCE_SET_KEY)
        records = []
        for instance in instances:
            record = await self.get_by_instance(instance)
            if record is not None:
                records.append(record)
        return records

    async def get_by_engine_id(self, engine_id: str) -> list[EngineRecord]:
        """Get all instances for a engine_id."""
        instances = await self._redis.smembers(
            f"{UNIFIED_RUNTIME_SET_PREFIX}{engine_id}"
        )
        records = []
        for instance in instances:
            record = await self.get_by_instance(instance)
            if record is not None:
                records.append(record)
        return records

    async def get_by_stage(self, stage: str) -> list[EngineRecord]:
        """Get all engines for a pipeline stage."""
        instances = await self._redis.smembers(f"{UNIFIED_STAGE_SET_PREFIX}{stage}")
        records = []
        for instance in instances:
            record = await self.get_by_instance(instance)
            if record is not None:
                records.append(record)
        return records

    async def get_available(
        self,
        *,
        stage: str | None = None,
        interface: str | None = None,
        engine_id: str | None = None,
        model: str | None = None,
        valid_engine_ids: set[str] | None = None,
    ) -> list[EngineRecord]:
        """Get available engines matching filters.

        Args:
            stage: Filter by pipeline stage
            interface: Filter by interface ("batch" or "realtime")
            engine_id: Filter by engine_id framework
            model: Filter by loaded model
            valid_engine_ids: Only consider engines with these engine_ids

        Returns:
            Available engines sorted by available capacity (descending)
        """
        if stage:
            candidates = await self.get_by_stage(stage)
        elif engine_id:
            candidates = await self.get_by_engine_id(engine_id)
        else:
            candidates = await self.get_all()

        available = []
        for record in candidates:
            if not record.is_available:
                continue

            if interface and not record.supports_interface(interface):
                continue

            if engine_id and record.engine_id != engine_id:
                continue

            if (
                valid_engine_ids is not None
                and record.engine_id not in valid_engine_ids
            ):
                continue

            if model is not None:
                if record.models_loaded and model in record.models_loaded:
                    pass  # Model already loaded
                elif engine_id and record.engine_id == engine_id:
                    pass  # Runtime matches, can load model dynamically
                elif (
                    record.capabilities
                    and record.capabilities.model_variants
                    and model in record.capabilities.model_variants
                ):
                    pass  # Engine declares model as supported variant
                else:
                    continue

            available.append(record)

        # Sort by available capacity descending; prefer model-loaded
        if model:
            available.sort(
                key=lambda r: (
                    model not in (r.models_loaded or []),
                    -r.available_capacity,
                )
            )
        else:
            available.sort(key=lambda r: -r.available_capacity)

        return available

    async def get_engine(self, engine_id: str) -> EngineRecord | None:
        """Get first available instance for a engine_id (compat with batch registry)."""
        instances = await self.get_by_engine_id(engine_id)
        if not instances:
            return None
        for inst in instances:
            if inst.is_available:
                return inst
        return instances[0]

    async def is_engine_available(self, engine_id: str) -> bool:
        """Check if a engine_id has at least one healthy instance."""
        instances = await self.get_by_engine_id(engine_id)
        return any(inst.is_healthy for inst in instances)

    async def mark_instance_offline(self, instance: str) -> None:
        """Mark an engine instance as offline."""
        instance_key = f"{UNIFIED_INSTANCE_KEY_PREFIX}{instance}"
        await self._redis.hset(instance_key, "status", "offline")
        logger.warning("unified_registry_marked_offline", instance=instance)


class UnifiedRegistryWriter:
    """Sync writer for unified registry, used by batch engine runners.

    Batch engines use synchronous Redis, so this provides a sync interface
    for dual-write during the migration period.
    """

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._redis: Any = None

    def _get_redis(self) -> Any:
        """Get or create sync Redis connection."""
        if self._redis is None:
            import redis

            self._redis = redis.from_url(
                self._redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
        return self._redis

    def register(self, record: EngineRecord) -> None:
        """Write engine record to unified registry (sync)."""
        r = self._get_redis()
        instance_key = f"{UNIFIED_INSTANCE_KEY_PREFIX}{record.instance}"
        mapping = _record_to_mapping(record)

        r.hset(instance_key, mapping=mapping)
        r.expire(instance_key, HEARTBEAT_TTL)
        r.sadd(UNIFIED_INSTANCE_SET_KEY, record.instance)
        r.sadd(f"{UNIFIED_RUNTIME_SET_PREFIX}{record.engine_id}", record.instance)
        r.sadd(f"{UNIFIED_STAGE_SET_PREFIX}{record.stage}", record.instance)

    def heartbeat(
        self,
        instance: str,
        *,
        status: str | None = None,
        active_batch: int | None = None,
        loaded_model: str | None = None,
        engine_id: str | None = None,
        stage: str | None = None,
    ) -> None:
        """Update heartbeat (sync).

        engine_id and stage are written on every heartbeat so that if the Redis key
        expires and is re-created by the heartbeat, the critical static fields are
        always present (preventing silent quarantine by _mapping_to_record).
        """
        r = self._get_redis()
        instance_key = f"{UNIFIED_INSTANCE_KEY_PREFIX}{instance}"
        now = datetime.now(UTC).isoformat()

        mapping: dict[str, str] = {"last_heartbeat": now}
        if status is not None:
            mapping["status"] = status
        if active_batch is not None:
            mapping["active_batch"] = str(active_batch)
        if loaded_model is not None:
            mapping["loaded_model"] = loaded_model
        if engine_id is not None:
            mapping["engine_id"] = engine_id
        if stage is not None:
            mapping["stage"] = stage

        r.hset(instance_key, mapping=mapping)
        r.expire(instance_key, HEARTBEAT_TTL)

    def deregister(self, instance: str) -> None:
        """Remove engine from unified registry (sync)."""
        r = self._get_redis()
        instance_key = f"{UNIFIED_INSTANCE_KEY_PREFIX}{instance}"

        data = r.hmget(instance_key, "engine_id", "stage")
        engine_id = data[0]
        stage = data[1]

        r.delete(instance_key)
        r.srem(UNIFIED_INSTANCE_SET_KEY, instance)

        if engine_id:
            r.srem(f"{UNIFIED_RUNTIME_SET_PREFIX}{engine_id}", instance)
        if stage:
            r.srem(f"{UNIFIED_STAGE_SET_PREFIX}{stage}", instance)

    def close(self) -> None:
        """Close Redis connection."""
        if self._redis is not None:
            self._redis.close()
            self._redis = None
