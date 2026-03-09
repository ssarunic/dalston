# M33: Reliable Task Queues

|                  |                                                                                            |
| ---------------- | ------------------------------------------------------------------------------------------ |
| **Goal**         | Crash-resilient task processing with durable event transport and recovery                  |
| **Duration**     | 7-8 days                                                                                   |
| **Dependencies** | M28 (Engine Registry)                                                                      |
| **Deliverable**  | Redis Streams for tasks + events, reconciliation sweeper, atomic claims, HA orchestrator   |
| **Status**       | Completed                                                                                  |

## User Story

> *"When my transcription engine crashes mid-job (spot instance preemption, OOM, container restart), the task automatically recovers and completes when an engine comes back online. Jobs never get stuck in RUNNING forever."*

---

## Outcomes

| Scenario | Current | After M33 |
| -------- | ------- | --------- |
| Engine crashes mid-task | Job stuck forever | Task auto-recovers after 10 min |
| Task exceeds 20 min timeout | Nothing happens | Task failed, job proceeds/fails |
| Engine restarts after crash | Lost task | Claims stale tasks on startup |
| Multiple orchestrators (scanner) | Not supported | Leader election for scanner |
| Multiple orchestrators (events) | Duplicate DAGs possible | Atomic DB ownership claims |
| Max retries exceeded | Manual intervention | Job fails with clear error |
| Orchestrator down during job.created | Job stuck PENDING forever | Event replayed on restart |
| Orchestrator disconnected during task.completed | Dependents never advance | Event replayed, reconciler catches stragglers |
| Redis restart/crash | Queued tasks lost | AOF persistence + reconciler recovery |
| Job stuck due to missed event | Manual DB intervention | Reconciler auto-repairs within 5 min |

---

## Strategy: Redis Streams

Replace Redis Lists (`BRPOP`) with Redis Streams and Consumer Groups.

### Why Streams Over BRPOP?

| Feature | Lists (BRPOP) | Streams (XREADGROUP) |
| ------- | ------------- | -------------------- |
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

Implements `StreamMessage` and `PendingTask` dataclasses plus async helpers for stream group creation, task add/read/claim/ack, pending inspection, stream discovery via SCAN, and monitoring info.

*Implementation: see `dalston/common/streams.py`*

---

### 33.2: Engine SDK Migration

**Deliverables:**

- Update `dalston/engine_sdk/runner.py` to use Streams
- Claim stale tasks on startup (recovery)
- Acknowledge on completion

`EngineRunner._poll_and_process()` was updated to first attempt claiming stale tasks from dead engines (via heartbeat check), then fall back to reading new tasks via `XREADGROUP`. Tasks are always acknowledged after processing. A `claim_stale_from_dead_engines()` helper checks the PEL for idle tasks whose owning engine is no longer heartbeating before claiming them.

*Implementation: see `dalston/engine_sdk/runner.py`*

**Key insight:** Only steal tasks from dead engines. If engine is alive but slow, leave it alone.

**Stage from engine.yaml:**

The engine already reads its stage from `engine.yaml` capabilities. Use `capabilities.stages[0]` for stream name.

---

### 33.3: Orchestrator Scheduler Migration

**Deliverables:**

- Update `dalston/orchestrator/scheduler.py` to use `XADD`

Replaced `redis.lpush()` with `add_task()` (XADD) in `queue_task()`, passing the calculated timeout so it is stored in the stream message for the scanner to use.

*Implementation: see `dalston/orchestrator/scheduler.py`*

---

### 33.4: Stale Task Scanner

**Deliverables:**

- `dalston/orchestrator/scanner.py` — Background stale task scanner
- Runs in orchestrator, checks every 60 seconds
- Discovers streams dynamically (no hardcoded stage list)
- Fails tasks that exceeded max deliveries or absolute timeout

The scanner dynamically discovers all `dalston:stream:*` keys via SCAN, then iterates pending entries in each stream. Tasks exceeding `MAX_DELIVERIES` (3) or `ABSOLUTE_TIMEOUT_MS` (30 min) are failed in the DB and acknowledged from the stream. The scanner runs as a background task in the orchestrator main loop, gated by leader election, every 60 seconds.

*Implementation: see `dalston/orchestrator/scanner.py`*

---

### 33.5: Leader Election for Scanner

**Deliverables:**

- Simple Redis-based leader election
- Only one orchestrator runs the scanner at a time
- Others are standby

Uses a Redis key (`dalston:orchestrator:leader`) with `SET NX EX 30` for leader acquisition. The leader renews via `EXPIRE` on each scan cycle; other instances remain standby until the key expires.

*Implementation: see `dalston/orchestrator/leader.py`*

**Note:** Event handling remains concurrent across all orchestrators (no leader election for events). Section 33.8 ensures correctness via atomic DB ownership claims rather than event routing.

---

### 33.6: Task Cancellation Update

**Deliverables:**

- Update `remove_task_from_queue()` to work with Streams

`remove_task_from_stream()` searches the PEL for the target task and acknowledges it if found. If the task is unclaimed, engines check DB status before processing and skip cancelled tasks.

*Implementation: see `dalston/common/streams.py` and `dalston/engine_sdk/runner.py`*

---

### 33.7: Metrics

**Deliverables:**

- Update existing queue metrics to use stream info
- Add pending/stale task metrics

Updated `dalston_queue_depth` gauge to use stream info. Added new gauges (`dalston_tasks_pending`, `dalston_tasks_stale`) and counters (`dalston_task_recovery_total`, `dalston_task_redelivery_total` with stage/reason labels). Engines log `delivery_count` on every task processing for redelivery debugging.

*Implementation: see `dalston/metrics.py`*

---

### 33.8: Multi-Orchestrator Event Safety

**Problem:**

When multiple orchestrators run concurrently, they all receive the same events via Redis pub/sub broadcast. Without atomic ownership claims, race conditions cause:

1. **Duplicate DAG creation** - Two orchestrators both handle `job.created`, both see status=pending, both create tasks with different UUIDs
2. **Duplicate dependent queueing** - Two orchestrators both handle `task.completed`, both see dependent status=pending, both queue the same task

**Evidence from code review:**

- `handlers.py:121-132` - Non-atomic check for job status + task existence
- `handlers.py:368-377` - Non-atomic check-then-act for dependent task status
- `events.py:49` - Broadcast publish to all subscribers
- `models.py:247` - No uniqueness constraint on tasks table

**Deliverables:**

- Atomic job ownership claim in `handle_job_created()`
- Atomic task status transitions in `handle_task_completed()`
- DB migration adding uniqueness constraint for tasks

**1. Atomic job ownership:** `handle_job_created()` uses an `UPDATE ... WHERE status = PENDING RETURNING id` to atomically claim ownership. Only the orchestrator that wins the row update builds the DAG; others log "already claimed" and return.

**2. Atomic dependent task transition:** `handle_task_completed()` uses the same pattern to transition dependents from PENDING to READY, ensuring only one orchestrator queues each dependent task.

**3. DB uniqueness constraint:** A migration adds `UNIQUE (job_id, stage)` on the tasks table, preventing duplicate tasks even under race conditions. Per-channel stages (e.g., `transcribe_ch0`) are inherently unique by name.

*Implementation: see `dalston/orchestrator/handlers.py` and `alembic/versions/`*

Note: This constraint works because:

- Regular stages: one task per job (e.g., `prepare`, `merge`)
- Per-channel stages: include channel in name (e.g., `transcribe_ch0`, `transcribe_ch1`)

**4. Concurrency tests:** Tests verify that concurrent `handle_job_created()` calls produce exactly one DAG (no duplicate stages), and concurrent `handle_task_completed()` calls queue each dependent exactly once.

*Implementation: see `tests/integration/test_multi_orchestrator.py`*

---

### 33.9: Reconciliation Sweeper

**Problem:**

Even with durable event transport, defense-in-depth requires a reconciler that detects and repairs stranded states. This catches edge cases where:

- Events are durable but processing fails silently
- Bugs in event handlers leave inconsistent state
- Partial failures during multi-step operations

**Deliverables:**

- `dalston/orchestrator/reconciler.py` — Periodic state reconciliation
- Runs in orchestrator under leader election (shares with scanner)
- Repairs stranded jobs and tasks from database state

**Implementation:** The reconciler runs two checks every 5 minutes (under leader election, sharing the scanner loop): (1) finds PENDING jobs with no tasks (missed `job.created`) and re-publishes the event, and (2) finds READY tasks not present in any stream and re-queues them. Both use `skip_locked` to avoid contention and emit metrics via `reconciler_repairs` counter.

*Implementation: see `dalston/orchestrator/reconciler.py`*

---

### 33.10: Durable Event Transport

**Problem:**

Redis Pub/Sub is fire-and-forget. If the orchestrator is disconnected when an event is published, that event is lost forever. This causes jobs to get stuck in PENDING state.

**Evidence from current code:**

- Events published via `redis.publish()` at `dalston/common/events.py:49`
- Orchestrator subscribes at `dalston/orchestrator/main.py:153`
- No acknowledgment, no replay, no persistence

**Deliverables:**

- Migrate event publishing from Pub/Sub to Redis Streams
- Migrate orchestrator subscription to consumer groups
- Replay missed events on orchestrator startup

**Stream naming:**

- Event stream: `dalston:events` (single stream for all event types)
- Consumer group: `orchestrators`
- Each orchestrator instance is a consumer

The events module provides: `ensure_events_group()` (idempotent consumer group creation), `add_event()` (XADD with trace context injection), `read_events()` (XREADGROUP consumer), `ack_event()` (XACK), and `claim_pending_events()` (XAUTOCLAIM for startup recovery). All `publish_*` functions were migrated to use `add_event()` instead of `redis.publish()`.

*Implementation: see `dalston/common/events.py`*

The orchestrator main loop generates a unique consumer ID per instance, claims pending events from dead orchestrators on startup, then enters a blocking read loop with explicit acknowledgment after each event is dispatched.

*Implementation: see `dalston/orchestrator/main.py`*

**Key behavioral changes:**

| Aspect | Before (Pub/Sub) | After (Streams) |
| ------ | ---------------- | --------------- |
| Delivery guarantee | At-most-once | At-least-once |
| Orchestrator restart | Missed events lost | Replay pending events |
| Multiple orchestrators | All receive all events | Each event processed once |
| Acknowledgment | None | Explicit XACK |

**Note on idempotency:** Handlers already use atomic DB claims (33.8), so at-least-once delivery is safe. Duplicate event delivery results in "already claimed" log, not duplicate work.

---

### 33.11: Redis AOF Configuration

**Problem:**

Redis is configured without AOF persistence. On Redis restart, all queued tasks and pending events are lost.

**Evidence:**

Current `docker-compose.yml` Redis configuration lacks persistence settings.

**Deliverables:**

- Update docker-compose.yml Redis configuration
- Add Redis persistence volume

Redis is configured with `appendonly yes`, `appendfsync everysec`, AOF auto-rewrite at 100%/64MB minimum, a persistent data volume, and a health check.

*Implementation: see `docker-compose.yml` (redis service)*

**Configuration options:**

| Setting | Value | Rationale |
| ------- | ----- | --------- |
| `appendonly` | yes | Enable AOF persistence |
| `appendfsync` | everysec | Fsync every second (good balance of durability/perf) |
| `auto-aof-rewrite-percentage` | 100 | Rewrite when AOF doubles in size |
| `auto-aof-rewrite-min-size` | 64mb | Don't rewrite until AOF reaches 64MB |

**Trade-off:** `appendfsync everysec` can lose up to 1 second of writes on crash. For stronger guarantees, use `appendfsync always` (slower).

---

## Verification

- [ ] **Engine crash recovery:** Kill an engine mid-task, restart it, confirm the stale task is auto-claimed and the job completes (check logs for `claimed_stale`)
- [ ] **Multi-orchestrator safety:** Scale to 2 orchestrators, submit 5 concurrent jobs, verify no duplicate tasks in DB (`SELECT job_id, stage, COUNT(*) ... HAVING COUNT(*) > 1` returns 0 rows)
- [ ] **Event durability:** Submit a job, immediately stop the orchestrator, restart it, confirm the job progresses (not stuck in PENDING)
- [ ] **Reconciler repair:** Insert a stranded PENDING job directly in DB, wait for reconciler cycle (~5 min), confirm tasks were created
- [ ] **Redis persistence:** Note stream lengths, restart Redis, verify AOF restored the same stream lengths

---

## Upgrade Path

**Prerequisites:** Ensure all legacy queues (`dalston:queue:*`) are empty before upgrade (check with `LLEN`).

**Upgrade:**

1. Stop all engines and orchestrator
2. Deploy new code
3. Start orchestrator (creates consumer groups on first task)
4. Start engines

No data migration needed.

---

## Checkpoint

**Task Queue Streams (33.1-33.7):**

- [x] `dalston/common/streams.py` with helper functions
- [x] `discover_streams()` finds all stage streams dynamically
- [x] Engine SDK uses `XREADGROUP` + `XAUTOCLAIM` + `XACK`
- [x] Engine SDK checks heartbeat before stealing (only claim from dead engines)
- [x] Orchestrator uses `XADD` instead of `LPUSH`
- [x] Stale scanner discovers streams and fails abandoned tasks
- [x] Leader election for scanner
- [x] Task cancellation works with streams
- [x] `StreamMessage` includes `delivery_count` from PEL
- [x] Engine logs `delivery_count` on task processing
- [x] Redelivery metric tracks recovery reasons
- [x] Metrics updated

**Multi-Orchestrator Safety (33.8):**

- [x] Atomic job ownership claim in `handle_job_created()`
- [x] Atomic task status transition in `handle_task_completed()`
- [x] DB migration: unique constraint on `tasks(job_id, stage)`
- [x] Concurrency tests for multi-orchestrator scenarios

**Reconciliation Sweeper (33.9):**

- [x] `dalston/orchestrator/reconciler.py` with stranded state detection
- [x] Reconciler finds PENDING jobs with no tasks
- [x] Reconciler finds READY tasks not in streams
- [x] Reconciler runs under leader election
- [x] Metrics for reconciliation repairs

**Durable Event Transport (33.10):**

- [x] `dalston/common/events.py` migrated to Redis Streams
- [x] Event consumer group `orchestrators` created on startup
- [x] Orchestrator replays pending events on restart
- [x] All `publish_*` functions use `XADD`
- [x] Events acknowledged after successful handler execution
- [x] Integration test: orchestrator down during job.created

**Redis AOF (33.11):**

- [x] `docker-compose.yml` Redis configured with AOF
- [x] Redis data volume for persistence
- [x] Integration test: Redis restart preserves streams

**Acceptance Criteria:**

- [x] Orchestrator down during event → processes after restart
- [x] Redis restart → system recovers without manual intervention
- [x] No job stuck indefinitely due to missed control-plane messages
- [x] Integration tests simulate orchestrator/Redis restarts

---

## Configuration

Key tuning parameters (all configurable via environment variables):

| Parameter | Default | Purpose |
| --------- | ------- | ------- |
| `STALE_THRESHOLD_MS` | 600000 (10 min) | Task idle threshold (only claims from dead engines) |
| `SCAN_INTERVAL_S` | 60 | Scanner check frequency |
| `MAX_DELIVERIES` | 3 | Fail task after N attempts |
| `ABSOLUTE_TIMEOUT_MS` | 1800000 (30 min) | Hard task timeout |
| `LEADER_TTL_S` | 30 | Leader election key TTL |
| `RECONCILE_INTERVAL_S` | 300 (5 min) | Reconciler check frequency |
| `STRANDED_JOB_THRESHOLD_S` | 300 (5 min) | PENDING job with no tasks threshold |
| `STRANDED_TASK_THRESHOLD_S` | 300 (5 min) | READY task not in stream threshold |
| `EVENT_CLAIM_MIN_IDLE_MS` | 60000 (1 min) | Claim events from dead orchestrators |

