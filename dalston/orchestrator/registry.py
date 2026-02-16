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
ENGINE_SET_KEY = "dalston:batch:engines"
ENGINE_KEY_PREFIX = "dalston:batch:engine:"

# Heartbeat timeout - engine considered offline if no heartbeat within this period
HEARTBEAT_TIMEOUT_SECONDS = 60


@dataclass
class BatchEngineState:
    """Batch engine state read from Redis.

    Attributes:
        engine_id: Unique identifier (e.g., "faster-whisper")
        stage: Pipeline stage this engine handles (e.g., "transcribe")
        queue_name: Redis queue name this engine polls
        status: Current status ("idle", "processing", "offline")
        current_task: Task ID currently being processed, or None
        last_heartbeat: Last heartbeat timestamp
        registered_at: Engine registration timestamp
        capabilities: Engine capabilities for validation (M29)
    """

    engine_id: str
    stage: str
    queue_name: str
    status: str
    current_task: str | None
    last_heartbeat: datetime
    registered_at: datetime
    capabilities: EngineCapabilities | None = None

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

        Returns:
            List of all engine states
        """
        engine_ids = await self._redis.smembers(ENGINE_SET_KEY)
        engines = []

        for engine_id in engine_ids:
            engine = await self.get_engine(engine_id)
            if engine is not None:
                engines.append(engine)

        return engines

    async def get_engine(self, engine_id: str) -> BatchEngineState | None:
        """Get specific engine state.

        Args:
            engine_id: Engine identifier

        Returns:
            BatchEngineState if found, None otherwise
        """
        engine_key = f"{ENGINE_KEY_PREFIX}{engine_id}"
        data = await self._redis.hgetall(engine_key)

        if not data:
            return None

        return self._parse_engine_state(engine_id, data)

    async def get_engines_for_stage(self, stage: str) -> list[BatchEngineState]:
        """Get all engines that handle a specific pipeline stage.

        Args:
            stage: Pipeline stage (e.g., "transcribe", "align", "diarize")

        Returns:
            List of engines for the given stage
        """
        engines = await self.get_engines()
        return [e for e in engines if e.stage == stage]

    async def is_engine_available(self, engine_id: str) -> bool:
        """Check if an engine is registered and healthy.

        Args:
            engine_id: Engine identifier

        Returns:
            True if engine is available for task routing
        """
        engine = await self.get_engine(engine_id)
        if engine is None:
            return False
        return engine.is_available

    async def mark_engine_offline(self, engine_id: str) -> None:
        """Mark engine as offline due to stale heartbeat.

        Args:
            engine_id: Engine identifier
        """
        engine_key = f"{ENGINE_KEY_PREFIX}{engine_id}"
        await self._redis.hset(engine_key, "status", "offline")
        logger.warning("batch_engine_marked_offline", engine_id=engine_id)

    def _parse_engine_state(self, engine_id: str, data: dict) -> BatchEngineState:
        """Parse engine state from Redis hash data."""
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
                    engine_id=engine_id,
                    capabilities_json=capabilities_json[:100]
                    if capabilities_json
                    else None,
                )

        return BatchEngineState(
            engine_id=engine_id,
            stage=data.get("stage", "unknown"),
            queue_name=data.get("queue_name", ""),
            status=data.get("status", "offline"),
            current_task=current_task if current_task else None,
            last_heartbeat=self._parse_datetime(data.get("last_heartbeat")),
            registered_at=self._parse_datetime(data.get("registered_at")),
            capabilities=capabilities,
        )

    def _parse_datetime(self, value: str | None) -> datetime:
        """Parse ISO datetime string."""
        if value:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                pass
        return datetime.now(UTC)
