# Batch Engine Registry

## Problem

The orchestrator blindly pushes tasks to Redis queues without knowing if engines are running. If an engine isn't available, tasks sit silently for up to 24 hours until metadata TTL expires. This is unacceptable for AWS deployment where containers restart and spot instances disappear.

The realtime side already solved this with `session_router/registry.py` + `realtime_sdk/registry.py`. Batch needs the same pattern.

## Current State

### What exists

**Batch engine heartbeat** (runner.py:229-250):

```python
self.redis_client.hset(
    self.heartbeat_key,  # dalston:batch_engine:{engine_id}:heartbeat
    mapping={
        "engine_id": self.engine_id,
        "stage": getattr(self.engine, "stage", "unknown"),
        "last_seen": datetime.now(UTC).isoformat(),
        "status": "processing" if current_task else "idle",
        "current_task": current_task or "",
    },
)
self.redis_client.expire(self.heartbeat_key, self.HEARTBEAT_TTL)  # 60s
```

**Realtime registry pattern** (realtime_sdk/registry.py + session_router/registry.py):

- Client-side: `WorkerRegistry.register()`, `heartbeat()`, `unregister()`
- Server-side: `WorkerRegistry.get_workers()`, `get_available_workers()`, `mark_worker_offline()`
- Redis keys: SET for all workers, HASH per worker with full state

### What's missing

1. **No engine SET** — No `dalston:batch:engines` to enumerate all registered engines
2. **No registration** — Engines just start writing heartbeats, no formal registration
3. **No server-side registry** — Orchestrator has no way to query engine availability
4. **Minimal heartbeat data** — Missing: queue_name, capabilities, queue_depth

## Design

### Redis Key Schema

```
dalston:batch:engines                          # SET of all engine_ids
dalston:batch:engine:{engine_id}               # HASH with engine state
dalston:batch:engine:{engine_id}:queue_depth   # STRING (optional, updated by orchestrator)
```

Engine HASH fields:

```
engine_id: str           # e.g., "faster-whisper"
stage: str               # e.g., "transcribe"
queue_name: str          # e.g., "dalston:queue:faster-whisper"
status: str              # "idle" | "processing" | "offline"
current_task: str        # task_id or empty
last_heartbeat: str      # ISO timestamp
registered_at: str       # ISO timestamp
```

### Components

#### 1. BatchEngineRegistry (client-side, in engine_sdk/)

Location: `dalston/engine_sdk/registry.py`

Mirrors `realtime_sdk/registry.py` pattern:

```python
@dataclass
class BatchEngineInfo:
    engine_id: str
    stage: str
    queue_name: str

class BatchEngineRegistry:
    """Client for batch engine registration."""

    async def register(self, info: BatchEngineInfo) -> None
    async def heartbeat(self, engine_id: str, status: str, current_task: str | None) -> None
    async def unregister(self, engine_id: str) -> None
    async def close(self) -> None
```

#### 2. BatchEngineRegistry (server-side, in orchestrator/)

Location: `dalston/orchestrator/registry.py`

Mirrors `session_router/registry.py` pattern:

```python
@dataclass
class BatchEngineState:
    engine_id: str
    stage: str
    queue_name: str
    status: str
    current_task: str | None
    last_heartbeat: datetime
    registered_at: datetime

    @property
    def is_available(self) -> bool:
        """Engine is available if heartbeat is fresh (< 60s)."""
        age = (datetime.now(UTC) - self.last_heartbeat).total_seconds()
        return age < 60 and self.status != "offline"

class BatchEngineRegistry:
    """Server-side registry for querying batch engine state."""

    async def get_engines(self) -> list[BatchEngineState]
    async def get_engine(self, engine_id: str) -> BatchEngineState | None
    async def get_engines_for_stage(self, stage: str) -> list[BatchEngineState]
    async def is_engine_available(self, engine_id: str) -> bool
    async def mark_engine_offline(self, engine_id: str) -> None
```

#### 3. EngineRunner Changes

Location: `dalston/engine_sdk/runner.py`

Changes to existing code:

```python
class EngineRunner:
    def __init__(self, engine: Engine) -> None:
        # ... existing code ...
        self._registry: BatchEngineRegistry | None = None

    def run(self) -> None:
        # ... existing setup ...

        # NEW: Register with registry before starting heartbeat
        self._registry = BatchEngineRegistry(self.redis_url)
        asyncio.run(self._registry.register(BatchEngineInfo(
            engine_id=self.engine_id,
            stage=getattr(self.engine, "stage", "unknown"),
            queue_name=self.queue_key,
        )))

        # ... existing heartbeat and loop ...

    def stop(self) -> None:
        self._running = False
        # NEW: Unregister on shutdown
        if self._registry:
            asyncio.run(self._registry.unregister(self.engine_id))
```

The existing `_heartbeat_loop` changes to use registry:

```python
def _heartbeat_loop(self) -> None:
    while self._running:
        try:
            with self._task_lock:
                current_task = self._current_task_id

            # Use registry instead of direct Redis
            asyncio.run(self._registry.heartbeat(
                engine_id=self.engine_id,
                status="processing" if current_task else "idle",
                current_task=current_task,
            ))
        except Exception as e:
            logger.warning("heartbeat_failed", error=str(e))
        time.sleep(self.HEARTBEAT_INTERVAL)
```

#### 4. Scheduler Changes

Location: `dalston/orchestrator/scheduler.py`

Add engine availability check before queuing:

```python
async def queue_task(
    redis: Redis,
    task: Task,
    settings: Settings,
    registry: BatchEngineRegistry,  # NEW parameter
    previous_outputs: dict[str, Any] | None = None,
    audio_metadata: dict[str, Any] | None = None,
) -> None:
    # NEW: Check engine availability before queuing
    if not await registry.is_engine_available(task.engine_id):
        raise EngineUnavailableError(
            f"Engine '{task.engine_id}' is not available. "
            f"No healthy engine registered for stage '{task.stage}'."
        )

    # ... existing queue logic ...
```

#### 5. Handler Changes

Location: `dalston/orchestrator/handlers.py`

Pass registry to queue_task calls:

```python
# In handle_job_created and handle_task_completed:
await queue_task(
    redis=redis,
    task=task,
    settings=settings,
    registry=registry,  # NEW
    previous_outputs=previous_outputs,
)
```

#### 6. Orchestrator Main Changes

Location: `dalston/orchestrator/main.py`

Initialize registry and pass to handlers:

```python
async def orchestrator_loop() -> None:
    # ... existing setup ...

    # NEW: Initialize batch engine registry
    batch_registry = BatchEngineRegistry(redis)

    # Pass to dispatch
    await _dispatch_event(message["data"], redis, settings, batch_registry)
```

### Error Handling

New exception in `dalston/orchestrator/exceptions.py`:

```python
class EngineUnavailableError(Exception):
    """Raised when a required engine is not available."""

    def __init__(self, message: str, engine_id: str, stage: str):
        super().__init__(message)
        self.engine_id = engine_id
        self.stage = stage
```

When this error occurs:

1. Task is NOT queued to Redis
2. Task status remains PENDING (not READY)
3. Job status changes to FAILED with clear error message
4. Webhook fires with `transcription.failed` event

### Graceful Degradation

For backwards compatibility during rollout:

```python
async def is_engine_available(self, engine_id: str) -> bool:
    """Check if engine is registered and healthy."""
    engine = await self.get_engine(engine_id)
    if engine is None:
        # No registration found - check for legacy heartbeat
        legacy_key = f"dalston:batch_engine:{engine_id}:heartbeat"
        if await self._redis.exists(legacy_key):
            logger.warning(
                "engine_using_legacy_heartbeat",
                engine_id=engine_id,
                hint="Upgrade engine to use new registry",
            )
            return True
        return False
    return engine.is_available
```

This allows old engines to keep working while new ones use the registry.

## Implementation Order

### Step 1: Client-side registry (engine_sdk/registry.py)

Create `BatchEngineRegistry` client class mirroring realtime pattern.

Files:

- NEW: `dalston/engine_sdk/registry.py`

Tests:

- NEW: `tests/unit/test_batch_registry.py`

### Step 2: Update EngineRunner to use registry

Modify runner.py to register/unregister and use registry for heartbeats.

Files:

- MODIFY: `dalston/engine_sdk/runner.py`

Tests:

- MODIFY: `tests/unit/test_engine_sdk_types.py` (if runner tests exist)

### Step 3: Server-side registry (orchestrator/registry.py)

Create read-only registry for orchestrator to query engine state.

Files:

- NEW: `dalston/orchestrator/registry.py`
- NEW: `dalston/orchestrator/exceptions.py` (or add to existing)

Tests:

- NEW: `tests/unit/test_orchestrator_registry.py`

### Step 4: Integrate with scheduler

Add availability check to `queue_task()`.

Files:

- MODIFY: `dalston/orchestrator/scheduler.py`

Tests:

- MODIFY: `tests/unit/test_dag.py` or create scheduler tests

### Step 5: Integrate with handlers and main

Wire registry through orchestrator main loop and handlers.

Files:

- MODIFY: `dalston/orchestrator/main.py`
- MODIFY: `dalston/orchestrator/handlers.py`

Tests:

- MODIFY: `tests/integration/test_batch_api.py`

### Step 6: Add /engines API endpoint (optional, for observability)

Expose engine registry state via Gateway API.

Files:

- NEW: `dalston/gateway/api/v1/engines.py`
- MODIFY: `dalston/gateway/api/v1/router.py`

Tests:

- NEW: `tests/integration/test_engines_api.py`

## Verification

After implementation:

1. **Start orchestrator without engines** — job creation should fail immediately with clear error
2. **Start engine, submit job** — should succeed
3. **Stop engine mid-job** — running task completes, next task fails with engine unavailable
4. **Restart engine** — new jobs should route correctly within 60s (heartbeat TTL)

## Migration

1. Deploy new engine images with registry support
2. Deploy new orchestrator with registry checks
3. Old engines continue working via legacy heartbeat fallback
4. Monitor logs for `engine_using_legacy_heartbeat` warnings
5. Upgrade remaining engines
6. Remove legacy fallback after all engines upgraded

## Not In Scope

- GPU memory tracking (batch engines don't report this yet)
- Dynamic engine routing based on load (future work)
- Engine capabilities discovery from engine.yaml (separate milestone)
- Queue depth tracking (could be added to heartbeat later)
