# M33: Reliable Task Queues

|               |                                                                                 |
| ------------- | ------------------------------------------------------------------------------- |
| **Goal**      | Crash-resilient task processing with automatic recovery                         |
| **Duration**  | 4-5 days                                                                        |
| **Dependencies** | M28 (Engine Registry)                                                        |
| **Deliverable** | Redis Streams task queues, stale task recovery, HA orchestrator support       |
| **Status**    | Not Started                                                                     |

## User Story

> *"When my transcription engine crashes mid-job (spot instance preemption, OOM, container restart), the task automatically recovers and completes when an engine comes back online. Jobs never get stuck in RUNNING forever."*

---

## Outcomes

| Scenario | Current | After M33 |
|----------|---------|-----------|
| Engine crashes mid-task | Job stuck forever | Task auto-recovers after 10 min |
| Task exceeds 20 min timeout | Nothing happens | Task failed, job proceeds/fails |
| Engine restarts after crash | Lost task | Claims stale tasks on startup |
| Multiple orchestrators | Not supported | Leader election for scanner |
| Max retries exceeded | Manual intervention | Job fails with clear error |

---

## Strategy: Redis Streams

Replace Redis Lists (`BRPOP`) with Redis Streams and Consumer Groups.

### Why Streams Over BRPOP?

| Feature | Lists (BRPOP) | Streams (XREADGROUP) |
|---------|---------------|----------------------|
| Delivery tracking | None | Pending Entries List (PEL) |
| Who has the task? | Unknown | Tracked per message |
| Delivery count | Manual | Automatic |
| Stale detection | Impossible | `XPENDING` shows idle time |
| Recovery | Manual | `XAUTOCLAIM` in one command |

### Why Streams Over BLMOVE?

BLMOVE (move to processing list) works but requires:

- Separate metadata hash per task (`{engine_id, claimed_at, timeout_at}`)
- Manual delivery count tracking
- Custom scanner logic
- Race condition handling

Streams provide all of this built-in. Same outcome, less code.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           REDIS STREAMS TASK FLOW                                │
│                                                                                  │
│   Orchestrator                  Redis Stream                    Engines          │
│                                                                                  │
│   queue_task() ─────────────▶  XADD                                             │
│                               dalston:stream:transcribe                          │
│                               ┌─────────────────────────┐                        │
│                               │ msg-1: {task_id: abc}   │                        │
│                               │ msg-2: {task_id: def}   │                        │
│                               └─────────────────────────┘                        │
│                                                                                  │
│                               Consumer Group: "engines"                          │
│                               ┌─────────────────────────┐                        │
│                               │ Pending Entries List:   │                        │
│                               │                         │                        │
│                               │ msg-1 → engine-1        │◀─── engine-1 claimed  │
│                               │         idle: 45s       │     via XREADGROUP    │
│                               │         deliveries: 1   │                        │
│                               └─────────────────────────┘                        │
│                                          │                                       │
│                                          │ engine-1 crashes                     │
│                                          │ (stops heartbeating)                 │
│                                          ▼                                       │
│                               msg-1 stays in PEL, idle time grows               │
│                                          │                                       │
│                                          │ After 10 min idle                    │
│                                          │ + engine-1 not heartbeating...       │
│                                          ▼                                       │
│   engine-2 startup ─────────▶ XAUTOCLAIM claims msg-1                           │
│                               deliveries: 2                                      │
│                                          │                                       │
│                                          │ engine-2 completes                   │
│                                          ▼                                       │
│                               XACK removes from PEL                              │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Steps

### 33.1: Streams Helper Module

**Deliverables:**

- `dalston/common/streams.py` — Redis Streams helper functions

**Stream naming:**

- Pattern: `dalston:stream:{stage}` (e.g., `dalston:stream:transcribe`)
- Consumer group: `engines` (created on first use)

**Functions:**

```python
@dataclass
class StreamMessage:
    id: str              # Redis message ID (e.g., "1234567890-0")
    task_id: str
    job_id: str
    enqueued_at: datetime
    timeout_at: datetime

@dataclass
class PendingTask:
    message_id: str
    task_id: str
    consumer: str        # Engine ID that claimed it
    idle_ms: int         # Time since last delivery
    delivery_count: int

async def ensure_stream_group(redis: Redis, stage: str) -> None:
    """Create consumer group if it doesn't exist. Idempotent."""

async def add_task(redis: Redis, stage: str, task_id: str, job_id: str, timeout_s: int) -> str:
    """Add task to stream. Returns message ID."""

async def read_task(redis: Redis, stage: str, consumer: str, block_ms: int = 30000) -> StreamMessage | None:
    """Read next available task. Blocks until available or timeout."""

async def claim_stale_tasks(redis: Redis, stage: str, consumer: str, min_idle_ms: int, count: int = 1) -> list[StreamMessage]:
    """Claim tasks idle longer than min_idle_ms. Returns claimed messages."""

async def claim_tasks_by_id(redis: Redis, stage: str, consumer: str, message_ids: list[str]) -> list[StreamMessage]:
    """Claim specific messages by ID. Used for selective recovery."""

async def ack_task(redis: Redis, stage: str, message_id: str) -> None:
    """Acknowledge task completion. Removes from PEL."""

async def get_pending(redis: Redis, stage: str) -> list[PendingTask]:
    """Get all pending tasks with metadata."""

async def discover_streams(redis: Redis) -> list[str]:
    """Discover all task streams via SCAN. Returns stream keys."""

async def get_stream_info(redis: Redis, stage: str) -> dict:
    """Get stream length, pending count, consumer info. For monitoring."""
```

---

### 33.2: Engine SDK Migration

**Deliverables:**

- Update `dalston/engine_sdk/runner.py` to use Streams
- Claim stale tasks on startup (recovery)
- Acknowledge on completion

**Changes to `EngineRunner`:**

```python
# Constants
STALE_THRESHOLD_MS = 10 * 60 * 1000  # 10 minutes
MAX_DELIVERIES = 3

def _poll_and_process(self) -> None:
    # 1. Try to claim stale tasks from DEAD engines only
    stale = claim_stale_from_dead_engines(
        self.redis_client,
        self.registry,
        stage=self.stage,
        consumer=self.engine_id,
        min_idle_ms=STALE_THRESHOLD_MS,
        count=1,
    )

    if stale:
        message = stale[0]
        logger.info("claimed_stale_task", task_id=message.task_id, delivery_count=...)
    else:
        # 2. No stale tasks - read new ones
        message = read_task(
            self.redis_client,
            stage=self.stage,
            consumer=self.engine_id,
            block_ms=30000,
        )

    if not message:
        return  # Timeout, no task

    # 3. Process task
    try:
        self._process_task(message.task_id)
    finally:
        # 4. Always ack - failure handling is via task.failed event
        ack_task(self.redis_client, self.stage, message.id)


async def claim_stale_from_dead_engines(
    redis: Redis,
    registry: BatchEngineRegistry,
    stage: str,
    consumer: str,
    min_idle_ms: int,
    count: int = 1,
) -> list[StreamMessage]:
    """Only claim tasks from engines that are no longer heartbeating."""
    pending = await get_pending(redis, stage)
    claimable = []

    for task in pending:
        if task.idle_ms < min_idle_ms:
            continue

        # Check if the engine that has this task is still alive
        engine_alive = await registry.is_engine_available(task.consumer)

        if not engine_alive:
            # Engine is dead - safe to steal this task
            claimable.append(task.message_id)
            if len(claimable) >= count:
                break

    if not claimable:
        return []

    # Claim the tasks
    return await claim_tasks_by_id(redis, stage, consumer, claimable)
```

**Key insight:** Only steal tasks from dead engines. If engine is alive but slow, leave it alone.

**Stage from engine.yaml:**

The engine already reads its stage from `engine.yaml` capabilities. Use `capabilities.stages[0]` for stream name.

---

### 33.3: Orchestrator Scheduler Migration

**Deliverables:**

- Update `dalston/orchestrator/scheduler.py` to use `XADD`

**Changes to `queue_task()`:**

```python
# Replace:
await redis.lpush(queue_key, task_id_str)

# With:
message_id = await add_task(
    redis,
    stage=task.stage,
    task_id=task_id_str,
    job_id=job_id_str,
    timeout_s=calculated_timeout,
)
logger.info("task_queued", task_id=task_id_str, stream=f"dalston:stream:{task.stage}", message_id=message_id)
```

**Timeout calculation:**

Already implemented in `calculate_task_timeout()`. Pass to `add_task()` so it's stored in message for scanner to use.

---

### 33.4: Stale Task Scanner

**Deliverables:**

- `dalston/orchestrator/scanner.py` — Background stale task scanner
- Runs in orchestrator, checks every 60 seconds
- Discovers streams dynamically (no hardcoded stage list)
- Fails tasks that exceeded max deliveries or absolute timeout

**Stream discovery:**

```python
async def discover_streams(redis: Redis) -> list[str]:
    """Find all task streams in Redis."""
    streams = []
    cursor = 0

    while True:
        cursor, keys = await redis.scan(cursor, match="dalston:stream:*", count=100)
        streams.extend(keys)
        if cursor == 0:
            break

    return streams
```

**Scanner logic:**

```python
SCAN_INTERVAL_S = 60
MAX_DELIVERIES = 3
ABSOLUTE_TIMEOUT_MS = 30 * 60 * 1000  # 30 minutes

async def scan_stale_tasks(redis: Redis, db: AsyncSession) -> None:
    """Scan all active streams for stale tasks."""

    # Discover streams dynamically - works for any stage
    streams = await discover_streams(redis)

    for stream_key in streams:
        stage = stream_key.split(":")[-1]  # "dalston:stream:transcribe" → "transcribe"
        pending = await get_pending(redis, stage)

        for task in pending:
            # Task exceeded max delivery attempts
            if task.delivery_count >= MAX_DELIVERIES:
                await fail_task_in_db(
                    db, task.task_id,
                    error=f"Max retries exceeded (delivered {task.delivery_count} times)"
                )
                await ack_task(redis, stage, task.message_id)  # Remove from stream
                await publish_task_failed(redis, task.task_id, error)
                continue

            # Task exceeded absolute timeout (catches stuck engines)
            if task.idle_ms > ABSOLUTE_TIMEOUT_MS:
                await fail_task_in_db(
                    db, task.task_id,
                    error=f"Task timeout ({task.idle_ms // 1000}s idle)"
                )
                await ack_task(redis, stage, task.message_id)
                await publish_task_failed(redis, task.task_id, error)
```

**Integration with orchestrator main loop:**

```python
async def main():
    # Start scanner as background task
    asyncio.create_task(run_scanner_loop())

    # Existing event loop
    async for event in subscribe_events():
        await handle_event(event)

async def run_scanner_loop():
    while True:
        if await try_acquire_leader(redis, instance_id):
            await scan_stale_tasks(redis, db)
        await asyncio.sleep(SCAN_INTERVAL_S)
```

---

### 33.5: Leader Election for Scanner

**Deliverables:**

- Simple Redis-based leader election
- Only one orchestrator runs the scanner at a time
- Others are standby

**Implementation:**

```python
LEADER_KEY = "dalston:orchestrator:leader"
LEADER_TTL_S = 30

async def try_acquire_leader(redis: Redis, instance_id: str) -> bool:
    """Try to become leader. Returns True if successful or already leader."""
    # Try to acquire
    acquired = await redis.set(LEADER_KEY, instance_id, nx=True, ex=LEADER_TTL_S)
    if acquired:
        return True

    # Check if we're already leader
    current = await redis.get(LEADER_KEY)
    if current == instance_id:
        await redis.expire(LEADER_KEY, LEADER_TTL_S)  # Renew
        return True

    return False
```

**Note:** Event handling remains concurrent across all orchestrators. Only the scanner needs leader election to avoid duplicate failure events.

---

### 33.6: Task Cancellation Update

**Deliverables:**

- Update `remove_task_from_queue()` to work with Streams

**Implementation:**

```python
async def remove_task_from_stream(redis: Redis, stage: str, task_id: str) -> bool:
    """Remove a task from stream. Used during cancellation."""
    pending = await get_pending(redis, stage)

    for task in pending:
        if task.task_id == task_id:
            await ack_task(redis, stage, task.message_id)
            return True

    # Task not in pending - might be in stream but unclaimed
    # Engine will check DB status and skip cancelled tasks
    return False
```

**Engine side:** Check task status in PostgreSQL before processing. Skip if `CANCELLED`.

---

### 33.7: Metrics

**Deliverables:**

- Update existing queue metrics to use stream info
- Add pending/stale task metrics

**Metrics:**

```python
# Existing (update implementation)
dalston_queue_depth = Gauge(
    "dalston_queue_depth",
    "Number of tasks waiting in queue",
    ["stage"]
)

# New
dalston_tasks_pending = Gauge(
    "dalston_tasks_pending",
    "Number of tasks currently being processed",
    ["stage"]
)

dalston_tasks_stale = Gauge(
    "dalston_tasks_stale",
    "Number of tasks idle longer than threshold",
    ["stage"]
)

dalston_task_recovery_total = Counter(
    "dalston_task_recovery_total",
    "Number of stale tasks recovered",
    ["stage"]
)
```

---

## Verification

```bash
# 1. Start services
docker compose up -d gateway orchestrator redis \
  stt-batch-prepare stt-batch-transcribe-whisper-cpu stt-batch-merge

# 2. Submit a job
JOB_ID=$(curl -s -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@test_audio.mp3" | jq -r '.id')

# 3. Wait for transcribe task to start
sleep 5
curl -s http://localhost:8000/v1/audio/transcriptions/$JOB_ID | jq '.status'
# "running"

# 4. Check pending tasks in stream
docker compose exec redis redis-cli XPENDING dalston:stream:transcribe engines
# Should show 1 pending

# 5. Kill the engine mid-task
docker compose stop stt-batch-transcribe-whisper-cpu

# 6. Wait and check pending (task stays, idle time grows)
sleep 60
docker compose exec redis redis-cli XPENDING dalston:stream:transcribe engines - + 10
# Should show idle time > 60000ms

# 7. Restart engine - it should claim stale task
docker compose start stt-batch-transcribe-whisper-cpu

# 8. Job should complete
sleep 30
curl -s http://localhost:8000/v1/audio/transcriptions/$JOB_ID | jq '.status'
# "completed"

# 9. Check delivery count was incremented
docker compose logs stt-batch-transcribe-whisper-cpu | grep claimed_stale
```

---

## Upgrade Path

**Prerequisites:** Ensure all queues are empty before upgrade.

```bash
# Check legacy queue depths
docker compose exec redis redis-cli KEYS "dalston:queue:*"
# For each key:
docker compose exec redis redis-cli LLEN dalston:queue:transcribe
# All should return 0
```

**Upgrade:**

1. Stop all engines and orchestrator
2. Deploy new code
3. Start orchestrator (creates consumer groups on first task)
4. Start engines

No data migration needed.

---

## Checkpoint

- [ ] `dalston/common/streams.py` with helper functions
- [ ] `discover_streams()` finds all stage streams dynamically
- [ ] Engine SDK uses `XREADGROUP` + `XAUTOCLAIM` + `XACK`
- [ ] Engine SDK checks heartbeat before stealing (only claim from dead engines)
- [ ] Orchestrator uses `XADD` instead of `LPUSH`
- [ ] Stale scanner discovers streams and fails abandoned tasks
- [ ] Leader election for scanner
- [ ] Task cancellation works with streams
- [ ] Metrics updated
- [ ] Integration test for crash recovery

---

## Configuration

```bash
# Task recovery (engine)
STALE_THRESHOLD_MS=600000       # 10 min - consider tasks idle longer than this
                                # BUT only claim if engine is also not heartbeating

# Stale scanner (orchestrator)
SCAN_INTERVAL_S=60              # Check every 60 seconds
MAX_DELIVERIES=3                # Fail after 3 attempts
ABSOLUTE_TIMEOUT_MS=1800000     # 30 min - fail even if engine alive

# Leader election (orchestrator)
LEADER_TTL_S=30
```

---

## Files Changed

| File | Change |
|------|--------|
| `dalston/common/streams.py` | New - Redis Streams helpers |
| `dalston/engine_sdk/runner.py` | Replace `brpop` with `xreadgroup` |
| `dalston/orchestrator/scheduler.py` | Replace `lpush` with `xadd` |
| `dalston/orchestrator/scanner.py` | New - stale task scanner |
| `dalston/orchestrator/leader.py` | New - leader election |
| `dalston/orchestrator/main.py` | Add scanner loop |
| `dalston/metrics.py` | Add stream metrics |
