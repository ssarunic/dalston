# M28: Batch Engine Registry

|               |                                                                                           |
| ------------- | ----------------------------------------------------------------------------------------- |
| **Goal**      | Fail-fast when batch engines are unavailable instead of silent queue timeouts            |
| **Duration**  | 2-3 days                                                                                  |
| **Dependencies** | None (uses existing patterns from realtime registry)                                   |
| **Deliverable** | Engine registration, heartbeat reporting, orchestrator availability checks              |
| **Status**    | Complete                                                                                  |

## User Story

> *"As an operator deploying Dalston to AWS, I want the system to fail immediately with a clear error when a required engine isn't running, instead of silently queuing tasks that timeout after 24 hours."*

---

## Overview

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         BATCH ENGINE REGISTRY                                    │
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────────┐│
│  │                         ENGINE CONTAINERS                                    ││
│  │                                                                              ││
│  │   ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐          ││
│  │   │  faster-whisper  │  │  whisperx-align  │  │   pyannote-4.0   │          ││
│  │   │                  │  │                  │  │                  │          ││
│  │   │  EngineRunner    │  │  EngineRunner    │  │  EngineRunner    │          ││
│  │   │  └─ register()   │  │  └─ register()   │  │  └─ register()   │          ││
│  │   │  └─ heartbeat()  │  │  └─ heartbeat()  │  │  └─ heartbeat()  │          ││
│  │   │  └─ unregister() │  │  └─ unregister() │  │  └─ unregister() │          ││
│  │   └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘          ││
│  │            │                     │                     │                     ││
│  └────────────┼─────────────────────┼─────────────────────┼─────────────────────┘│
│               │                     │                     │                      │
│               ▼                     ▼                     ▼                      │
│  ┌─────────────────────────────────────────────────────────────────────────────┐│
│  │                              REDIS                                           ││
│  │                                                                              ││
│  │   dalston:batch:engines  ─────────────────────────────────────────────────  ││
│  │   SET { "faster-whisper", "whisperx-align", "pyannote-4.0" }                ││
│  │                                                                              ││
│  │   dalston:batch:engine:faster-whisper  ───────────────────────────────────  ││
│  │   HASH { engine_id, stage, queue_name, status, last_heartbeat, ... }        ││
│  │   TTL: 60 seconds (auto-expire if engine crashes)                           ││
│  │                                                                              ││
│  └─────────────────────────────────────────────────────────────────────────────┘│
│               ▲                                                                  │
│               │                                                                  │
│  ┌────────────┴────────────────────────────────────────────────────────────────┐│
│  │                           ORCHESTRATOR                                       ││
│  │                                                                              ││
│  │   BatchEngineRegistry (server-side)                                          ││
│  │   └─ is_engine_available("faster-whisper") ──► true/false                   ││
│  │   └─ get_engines_for_stage("transcribe") ──► [EngineState, ...]             ││
│  │                                                                              ││
│  │   queue_task()                                                               ││
│  │   └─ if not available: raise EngineUnavailableError                         ││
│  │   └─ else: push to Redis queue                                               ││
│  │                                                                              ││
│  └─────────────────────────────────────────────────────────────────────────────┘│
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Design Decisions

### Mirror Realtime Pattern

The realtime side already has a working registry (`session_router/registry.py` + `realtime_sdk/registry.py`). We mirror this pattern exactly:

- Client-side registry in SDK for engines to call
- Server-side registry in orchestrator to query
- Redis SET for enumeration, HASH per engine for state
- TTL-based expiration for crash detection

### Fail-Fast, Not Fail-Safe

When an engine is unavailable:

- Task is NOT queued to Redis
- Job transitions to FAILED immediately
- Clear error message: "Engine 'faster-whisper' is not available"
- Webhook fires with `transcription.failed` event

This is better than silent timeout because:

- Operator knows immediately
- No wasted queue storage
- Client can retry or alert

### No Backwards Compatibility

Legacy heartbeat format was removed entirely. All engines must use the new registry.

### Heartbeat TTL Strategy

- Heartbeat interval: 10 seconds (existing)
- TTL: 60 seconds
- Engine considered "stale" after 60s without heartbeat
- Matches existing `HEARTBEAT_TTL` in runner.py

---

## Redis Key Schema

```
dalston:batch:engines                    # SET of all registered engine_ids
dalston:batch:engine:{engine_id}         # HASH with engine state (TTL: 60s)
```

### Engine HASH Fields

| Field | Type | Description |
|-------|------|-------------|
| `engine_id` | string | e.g., "faster-whisper" |
| `stage` | string | e.g., "transcribe" |
| `queue_name` | string | e.g., "dalston:queue:faster-whisper" |
| `status` | string | "idle" or "processing" |
| `current_task` | string | task_id or empty |
| `last_heartbeat` | string | ISO timestamp |
| `registered_at` | string | ISO timestamp |

---

## Steps

### 28.1: Client-Side Registry

**Deliverables:**

- Create `dalston/engine_sdk/registry.py` with `BatchEngineRegistry` class
- Add `BatchEngineInfo` dataclass

**Implementation:**

```python
# dalston/engine_sdk/registry.py

BATCH_ENGINE_SET_KEY = "dalston:batch:engines"
BATCH_ENGINE_KEY_PREFIX = "dalston:batch:engine:"
BATCH_ENGINE_TTL = 60  # seconds

@dataclass
class BatchEngineInfo:
    engine_id: str
    stage: str
    queue_name: str

class BatchEngineRegistry:
    """Client for batch engine registration (mirrors realtime_sdk/registry.py)."""

    def __init__(self, redis_url: str) -> None
    async def register(self, info: BatchEngineInfo) -> None
    async def heartbeat(self, engine_id: str, status: str, current_task: str | None) -> None
    async def unregister(self, engine_id: str) -> None
    async def close(self) -> None
```

**Tests:**

- `tests/unit/test_batch_engine_registry.py`
  - Test registration adds to SET and creates HASH
  - Test heartbeat updates fields and refreshes TTL
  - Test unregister removes from SET and deletes HASH

---

### 28.2: Integrate Registry into EngineRunner

**Deliverables:**

- Modify `dalston/engine_sdk/runner.py` to use registry
- Register on startup, unregister on shutdown
- Replace direct Redis heartbeat with registry calls

**Changes to EngineRunner:**

```python
class EngineRunner:
    def __init__(self, engine: Engine) -> None:
        # ... existing code ...
        self._batch_registry: BatchEngineRegistry | None = None

    def run(self) -> None:
        # ... existing setup ...

        # Register with batch engine registry
        self._batch_registry = BatchEngineRegistry(self.redis_url)
        asyncio.run(self._batch_registry.register(BatchEngineInfo(
            engine_id=self.engine_id,
            stage=getattr(self.engine, "stage", "unknown"),
            queue_name=self.queue_key,
        )))

        self._start_heartbeat_thread()
        # ... rest of run() ...

    def _heartbeat_loop(self) -> None:
        """Send heartbeats via registry."""
        while self._running:
            try:
                with self._task_lock:
                    current_task = self._current_task_id

                asyncio.run(self._batch_registry.heartbeat(
                    engine_id=self.engine_id,
                    status="processing" if current_task else "idle",
                    current_task=current_task,
                ))
            except Exception as e:
                logger.warning("heartbeat_failed", error=str(e))
            time.sleep(self.HEARTBEAT_INTERVAL)

    def _stop_heartbeat_thread(self) -> None:
        if self._heartbeat_thread:
            self._heartbeat_thread = None
        # Unregister from registry
        if self._batch_registry:
            try:
                asyncio.run(self._batch_registry.unregister(self.engine_id))
                asyncio.run(self._batch_registry.close())
            except Exception:
                pass  # Best effort cleanup
```

**Backwards Compatibility:**

Remove the old direct Redis heartbeat code (`self.redis_client.hset(self.heartbeat_key, ...)`). The new registry uses different keys, so old and new can coexist during migration.

**Tests:**

- Modify existing runner tests to verify registration/unregistration

---

### 28.3: Server-Side Registry

**Deliverables:**

- Create `dalston/orchestrator/registry.py` with server-side `BatchEngineRegistry`
- Add `BatchEngineState` dataclass

**Implementation:**

```python
# dalston/orchestrator/registry.py

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

    def __init__(self, redis: Redis) -> None
    async def get_engines(self) -> list[BatchEngineState]
    async def get_engine(self, engine_id: str) -> BatchEngineState | None
    async def get_engines_for_stage(self, stage: str) -> list[BatchEngineState]
    async def is_engine_available(self, engine_id: str) -> bool
```

**Tests:**

- `tests/unit/test_orchestrator_registry.py`
  - Test get_engines returns all registered engines
  - Test is_engine_available returns False for missing/stale engines
  - Test get_engines_for_stage filters correctly

---

### 28.4: Add EngineUnavailableError

**Deliverables:**

- Add exception to `dalston/orchestrator/exceptions.py` (create if needed)

**Implementation:**

```python
# dalston/orchestrator/exceptions.py

class EngineUnavailableError(Exception):
    """Raised when a required engine is not available."""

    def __init__(self, engine_id: str, stage: str):
        self.engine_id = engine_id
        self.stage = stage
        super().__init__(
            f"Engine '{engine_id}' is not available. "
            f"No healthy engine registered for stage '{stage}'."
        )
```

---

### 28.5: Integrate with Scheduler

**Deliverables:**

- Modify `dalston/orchestrator/scheduler.py` to check availability before queuing

**Changes to queue_task:**

```python
async def queue_task(
    redis: Redis,
    task: Task,
    settings: Settings,
    registry: BatchEngineRegistry,  # NEW parameter
    previous_outputs: dict[str, Any] | None = None,
    audio_metadata: dict[str, Any] | None = None,
) -> None:
    """Queue a task for execution by its engine."""
    # Check engine availability before queuing
    if not await registry.is_engine_available(task.engine_id):
        raise EngineUnavailableError(
            engine_id=task.engine_id,
            stage=task.stage,
        )

    # ... existing queue logic ...
```

**Tests:**

- Test queue_task raises EngineUnavailableError when engine missing
- Test queue_task succeeds when engine is registered and healthy

---

### 28.6: Integrate with Handlers

**Deliverables:**

- Modify `dalston/orchestrator/handlers.py` to pass registry to queue_task
- Handle EngineUnavailableError by failing the job

**Changes to handle_job_created:**

```python
async def handle_job_created(
    job_id: UUID,
    db: AsyncSession,
    redis: Redis,
    settings: Settings,
    registry: BatchEngineRegistry,  # NEW parameter
) -> None:
    # ... existing code ...

    # Queue tasks with no dependencies
    for task in tasks:
        if not task.dependencies:
            try:
                await queue_task(
                    redis=redis,
                    task=task,
                    settings=settings,
                    registry=registry,
                    previous_outputs={},
                    audio_metadata=audio_metadata if task.stage == "prepare" else None,
                )
            except EngineUnavailableError as e:
                # Fail the job immediately
                job.status = JobStatus.FAILED.value
                job.error = str(e)
                job.completed_at = datetime.now(UTC)
                await db.commit()
                await publish_job_failed(redis, job_id, str(e))
                log.error("job_failed_engine_unavailable", error=str(e))
                return
```

Similar changes to `handle_task_completed` for dependent tasks.

---

### 28.7: Integrate with Orchestrator Main

**Deliverables:**

- Modify `dalston/orchestrator/main.py` to initialize registry and pass to handlers

**Changes:**

```python
async def orchestrator_loop() -> None:
    # ... existing setup ...

    # Initialize batch engine registry
    batch_registry = BatchEngineRegistry(redis)

    # ... event loop ...
    await _dispatch_event(message["data"], redis, settings, batch_registry)


async def _dispatch_event(
    data: str,
    redis: aioredis.Redis,
    settings,
    batch_registry: BatchEngineRegistry,  # NEW parameter
) -> None:
    # ... existing code ...

    if event_type == "job.created":
        await handle_job_created(job_id, db, redis, settings, batch_registry)

    elif event_type == "task.completed":
        await handle_task_completed(task_id, db, redis, settings, batch_registry)

    # ... etc ...
```

---

### 28.8: Legacy Heartbeat Fallback

**Status:** Skipped — No backwards compatibility needed (dev mode, no live clients).

---

### 28.9: Tests

**Deliverables:**

**Unit tests:**

- `tests/unit/test_batch_engine_registry.py` — Client-side registry
- `tests/unit/test_orchestrator_registry.py` — Server-side registry
- `tests/unit/test_scheduler.py` — Availability check in queue_task

**Integration tests:**

- `tests/integration/test_engine_availability.py`
  - Submit job without engines running → immediate failure
  - Start engine, submit job → success
  - Stop engine mid-pipeline → dependent tasks fail with clear error

---

## Verification

```bash
# 1. Start orchestrator WITHOUT any engines
docker compose up -d gateway orchestrator redis postgres minio minio-init

# 2. Submit a job — should fail immediately
JOB_ID=$(curl -s -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer dk_test" \
  -F "file=@test.mp3" | jq -r '.id')

# 3. Check job status — should be "failed" with clear error
curl -s http://localhost:8000/v1/audio/transcriptions/$JOB_ID \
  -H "Authorization: Bearer dk_test" | jq '{status, error}'
# {"status": "failed", "error": "Engine 'audio-prepare' is not available..."}

# 4. Start the engines
docker compose up -d stt-batch-prepare stt-batch-transcribe-whisper-cpu stt-batch-merge

# 5. Wait for registration (10s for first heartbeat)
sleep 12

# 6. Check engines are registered
docker compose exec redis redis-cli SMEMBERS dalston:batch:engines
# 1) "audio-prepare"
# 2) "faster-whisper"
# 3) "final-merger"

# 7. Submit another job — should succeed
JOB_ID=$(curl -s -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer dk_test" \
  -F "file=@test.mp3" | jq -r '.id')

# 8. Poll until complete
curl -s http://localhost:8000/v1/audio/transcriptions/$JOB_ID \
  -H "Authorization: Bearer dk_test" | jq '.status'
# "completed"

# 9. Stop an engine mid-flight and verify graceful handling
docker compose stop stt-batch-transcribe-whisper-cpu
# Submit job, wait for it to reach transcribe stage
# Should fail with "Engine 'faster-whisper' is not available"
```

---

## Checkpoint

- [x] `BatchEngineRegistry` client created in `engine_sdk/registry.py`
- [x] `EngineRunner` registers on startup, unregisters on shutdown
- [x] Heartbeat loop uses registry instead of direct Redis
- [x] `BatchEngineRegistry` server created in `orchestrator/registry.py`
- [x] `EngineUnavailableError` exception added
- [x] `queue_task()` checks availability before pushing to queue
- [x] Handlers catch `EngineUnavailableError` and fail job immediately
- [x] Legacy heartbeat fallback removed (no backwards compatibility needed)
- [x] Job fails immediately with clear error when engine unavailable
- [x] Job succeeds when engines are registered and healthy
- [x] Engine crash is detected within 60s (heartbeat TTL expiry)
- [x] All unit tests passing (19 tests)
- [ ] Integration tests verify end-to-end behavior (manual testing done)

---

## Files Changed

| File | Change |
|------|--------|
| `dalston/engine_sdk/registry.py` | NEW — Client-side BatchEngineRegistry |
| `dalston/engine_sdk/runner.py` | MODIFY — Use registry for registration/heartbeat |
| `dalston/engine_sdk/__init__.py` | MODIFY — Export registry classes |
| `dalston/orchestrator/registry.py` | NEW — Server-side BatchEngineRegistry |
| `dalston/orchestrator/exceptions.py` | NEW — EngineUnavailableError |
| `dalston/orchestrator/scheduler.py` | MODIFY — Check availability before queuing |
| `dalston/orchestrator/handlers.py` | MODIFY — Pass registry, handle errors |
| `dalston/orchestrator/main.py` | MODIFY — Initialize registry, pass to dispatch |
| `dalston/gateway/api/console.py` | MODIFY — Use new registry keys for engine status display |
| `tests/unit/test_batch_registry.py` | NEW — Client + server registry tests (19 tests) |

---

## Unblocked

This milestone enables:

- **AWS deployment** — Containers can restart without silent failures
- **Spot instance tolerance** — Engine disappearance is detected and reported
- **Operator visibility** — Clear errors instead of mysterious timeouts
- **Future work** — Foundation for dynamic engine routing (M29+)
