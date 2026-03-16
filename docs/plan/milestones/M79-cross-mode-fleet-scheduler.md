# M79: Cross-Mode Fleet Scheduler

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | Enable the orchestrator and session allocator to make globally-optimal scheduling decisions across batch and realtime modes |
| **Duration**       | 2–3 weeks                                                    |
| **Dependencies**   | M63 (Engine Unification), M64 (Registry Unification), M66 (Session Router Consolidation) |
| **Deliverable**    | Fleet-aware batch scheduler, mode-aware session allocator, queue depth visibility, cross-mode capacity API, metrics dashboard |
| **Status**         | Not Started                                                  |

## User Story

> *"As a platform operator, I want the system to automatically balance GPU utilization across batch and realtime workloads, so that realtime latency stays low while batch throughput is maximized during idle periods."*

---

## Motivation

Today, Dalston has two independent schedulers that share a worker pool but don't coordinate:

1. **Batch scheduler** (`scheduler.py:queue_task`) — checks only `is_engine_available()` (a boolean: "does at least one healthy instance exist?"), then pushes to a Redis Stream. It is capacity-blind: it doesn't know how loaded workers are, how deep the queue is, or whether realtime sessions are consuming GPU time.

2. **Session allocator** (`session_allocator.py:acquire_worker`) — picks the least-loaded worker by `available_capacity` but doesn't know about pending batch tasks in the Redis Stream queue.

3. **Admission controller** (`admission.py`) — enforces per-engine QoS (RT reservation, batch cap) but operates locally. The global schedulers can't see or influence these decisions.

The result: suboptimal GPU utilization and blind spots.

- A GPU worker with 2 active RT sessions and `rt_reservation=2` still gets batch tasks queued to it (the scheduler doesn't know it's RT-saturated). Those tasks sit in the stream until an RT session ends.
- When RT demand drops to zero, batch tasks don't backfill aggressively because the scheduler doesn't know RT slots are free.
- The session allocator picks the least-loaded worker by total capacity, but a worker with 3 pending batch tasks in its stream is effectively busier than one with 0 — the allocator can't see this.
- No metrics exist for cross-mode utilization, so operators can't tune `rt_reservation` / `batch_max_inflight` with data.

---

## Architecture

```
                          ┌──────────────────────────────┐
                          │     Fleet Capacity Tracker    │
                          │                               │
                          │  • Per-instance snapshot:     │
                          │    - active_batch/rt counts   │
                          │    - stream queue depth       │
                          │    - admission status         │
                          │    - loaded models            │
                          │                               │
                          │  • Fleet-level aggregates:    │
                          │    - total/available by mode  │
                          │    - queue depth by engine_id │
                          │    - utilization ratio        │
                          └──────────┬───────────────────┘
                                     │
                    ┌────────────────┼────────────────┐
                    │                │                │
              ┌─────▼─────┐   ┌─────▼──────┐   ┌────▼────────┐
              │  Batch     │   │  Session   │   │  Metrics    │
              │  Scheduler │   │  Allocator │   │  Exporter   │
              │            │   │            │   │             │
              │ queue_task │   │ acquire_   │   │ Prometheus  │
              │ + instance │   │ worker +   │   │ gauges for  │
              │   ranking  │   │ batch-load │   │ cross-mode  │
              │            │   │ awareness  │   │ utilization │
              └────────────┘   └────────────┘   └─────────────┘
```

Data sources feeding the Fleet Capacity Tracker:

- **Registry heartbeats** — `active_batch`, `active_realtime`, `status`, `models_loaded` (already exists)
- **Admission status** — `can_accept_batch`, `can_accept_rt`, `rt_reservation`, `batch_max_inflight` (new: published in heartbeat)
- **Stream queue depth** — `XLEN dalston:stream:{engine_id}` (new: periodically sampled)

---

## Steps

### 79.1: Expose Admission Status in Engine Heartbeats

**Files modified:**

- `dalston/engine_sdk/admission.py` — add `to_heartbeat_dict()` to `AdmissionController`
- `dalston/engine_sdk/runner.py` — include admission status in heartbeat payload
- `dalston/realtime_sdk/base.py` — same for realtime heartbeats (unified engines already have admission)
- `dalston/common/registry.py` — extend `EngineRecord` with admission fields

**Deliverables:**

Each engine heartbeat now includes:

```python
# Added to heartbeat payload
{
    "admission_can_accept_batch": True,
    "admission_can_accept_rt": True,
    "admission_active_batch": 2,
    "admission_active_rt": 1,
    "admission_rt_reservation": 2,
    "admission_batch_max_inflight": 4,
    "admission_total_capacity": 6,
}
```

`EngineRecord` gains these as optional fields (backward-compatible with engines that haven't upgraded):

```python
@dataclass
class EngineRecord:
    # ... existing fields ...

    # Admission status (populated by unified engines)
    admission_can_accept_batch: bool | None = None
    admission_can_accept_rt: bool | None = None
    admission_rt_reservation: int | None = None
    admission_batch_max_inflight: int | None = None
```

This step is read-only from the scheduler's perspective — no behavior change, just visibility.

---

### 79.2: Add Queue Depth Tracking

**Files modified:**

- `dalston/common/queue_depth.py` *(new)* — `QueueDepthTracker` that periodically samples `XLEN` per engine stream
- `dalston/orchestrator/handlers.py` — start tracker in orchestrator lifespan
- `dalston/common/registry.py` — add `queue_depth` field to `EngineRecord` or expose via separate Redis key

**Deliverables:**

A lightweight background loop (every 5s) that runs `XLEN dalston:stream:{engine_id}` for all active engine_ids and writes results to a Redis hash:

```python
class QueueDepthTracker:
    """Periodically samples Redis Stream lengths for batch queue depth."""

    async def _poll_loop(self) -> None:
        while self._running:
            engine_ids = await self._registry.get_active_engine_ids()
            pipe = self._redis.pipeline()
            for eid in engine_ids:
                pipe.xlen(f"dalston:stream:{eid}")
            depths = await pipe.execute()
            await self._redis.hset(
                "dalston:fleet:queue_depth",
                mapping=dict(zip(engine_ids, depths)),
            )
            await asyncio.sleep(self._interval)

    async def get_depth(self, engine_id: str) -> int:
        """Get current queue depth for an engine_id."""
        val = await self._redis.hget("dalston:fleet:queue_depth", engine_id)
        return int(val) if val else 0

    async def get_all_depths(self) -> dict[str, int]:
        """Get queue depths for all engine_ids."""
        raw = await self._redis.hgetall("dalston:fleet:queue_depth")
        return {k: int(v) for k, v in raw.items()}
```

No behavior change yet — just instrumentation.

---

### 79.3: Fleet Capacity View

**Files modified:**

- `dalston/common/fleet.py` *(new)* — `FleetCapacityView` that assembles a cross-mode snapshot
- `dalston/common/registry.py` — add `get_instances_for_engine()` method if missing

**Deliverables:**

A read-only view that combines registry records + queue depth into actionable signals:

```python
@dataclass(frozen=True)
class EngineFleetSnapshot:
    """Cross-mode capacity view for one engine_id."""
    engine_id: str
    instances: list[EngineRecord]
    queue_depth: int  # Pending batch tasks in stream

    @property
    def total_capacity(self) -> int:
        return sum(i.capacity for i in self.instances if i.is_healthy)

    @property
    def total_active_batch(self) -> int:
        return sum(i.active_batch for i in self.instances if i.is_healthy)

    @property
    def total_active_rt(self) -> int:
        return sum(i.active_realtime for i in self.instances if i.is_healthy)

    @property
    def fleet_available_capacity(self) -> int:
        return sum(i.available_capacity for i in self.instances if i.is_healthy)

    @property
    def batch_pressure(self) -> float:
        """Ratio of queued batch tasks to available batch capacity. >1.0 = backlogged."""
        batch_cap = sum(
            (i.admission_batch_max_inflight or i.capacity) - i.active_batch
            for i in self.instances if i.is_healthy
        )
        return self.queue_depth / max(batch_cap, 1)

    @property
    def rt_headroom(self) -> int:
        """Slots available for new RT sessions across fleet."""
        return sum(
            1 for i in self.instances
            if i.is_healthy and (i.admission_can_accept_rt is not False)
            and i.available_capacity > 0
        )


class FleetCapacityView:
    """Assembles cross-mode fleet snapshots from registry + queue depth."""

    def __init__(self, registry: UnifiedEngineRegistry, queue_tracker: QueueDepthTracker):
        self._registry = registry
        self._queue = queue_tracker

    async def snapshot(self, engine_id: str) -> EngineFleetSnapshot:
        instances = await self._registry.get_instances_for_engine(engine_id)
        depth = await self._queue.get_depth(engine_id)
        return EngineFleetSnapshot(
            engine_id=engine_id,
            instances=instances,
            queue_depth=depth,
        )
```

---

### 79.4: Fleet-Aware Batch Scheduling

**Files modified:**

- `dalston/orchestrator/scheduler.py` — modify `queue_task()` to use fleet snapshot for instance-level routing
- `dalston/orchestrator/handlers.py` — inject `FleetCapacityView` into scheduler

**Deliverables:**

Replace the boolean `is_engine_available()` check with fleet-aware logic:

```python
async def queue_task(self, task: Task, ...) -> None:
    # ... existing validation ...

    # NEW: Get fleet snapshot for this engine
    snapshot = await self._fleet.snapshot(task.engine_id)

    if not snapshot.instances:
        # No healthy instances at all
        if self._fail_fast:
            raise EngineUnavailableError(task.engine_id)
        # ... existing engine_needed event ...

    # NEW: Log fleet state for observability
    logger.info(
        "task_queue_fleet_state",
        engine_id=task.engine_id,
        queue_depth=snapshot.queue_depth,
        batch_pressure=snapshot.batch_pressure,
        rt_headroom=snapshot.rt_headroom,
        fleet_available=snapshot.fleet_available_capacity,
    )

    # Existing: push to Redis Stream
    await self._add_task(task, ...)
```

**Phase 1** (this step): Observability only — log fleet state at queue time but don't change routing. This lets us validate the signals before acting on them.

**Phase 2** (step 79.6): Act on the signals.

---

### 79.5: Batch-Aware Session Allocation

**Files modified:**

- `dalston/orchestrator/session_allocator.py` — add queue depth as a tiebreaker in `acquire_worker()`

**Deliverables:**

Enhance the least-loaded allocation to prefer workers with lower batch queue pressure:

```python
async def acquire_worker(self, ...) -> WorkerAllocation:
    available = await self._registry.get_available(
        interface="realtime", engine_id=engine_id, model=model,
    )

    if not available:
        raise NoAvailableWorkerError(...)

    # NEW: Enrich with queue depth signal
    # Workers sharing an engine_id share a stream, so queue depth
    # is per-engine_id, not per-instance. But workers with fewer
    # active_batch tasks will drain faster.
    for worker in available:
        worker._batch_drain_score = worker.active_batch

    # Sort: primary = available capacity (desc), secondary = batch load (asc)
    available.sort(
        key=lambda r: (
            model not in (r.models_loaded or []),  # Prefer warm model
            -r.available_capacity,                  # Prefer most headroom
            r._batch_drain_score,                   # Prefer less batch-loaded
        )
    )

    # ... existing atomic allocation logic ...
```

This is a conservative change: batch load is only a tiebreaker when two workers have equal capacity and model warmth. It doesn't change behavior when there's a clear winner.

---

### 79.6: Fleet-Aware Batch Instance Ranking

**Files modified:**

- `dalston/orchestrator/scheduler.py` — add optional instance hint to stream message
- `dalston/engine_sdk/runner.py` — respect instance hint when claiming tasks (soft preference, not hard routing)

**Deliverables:**

When queuing a batch task, the scheduler can now add a `preferred_instance` hint based on fleet state:

```python
async def _select_preferred_instance(
    self, snapshot: EngineFleetSnapshot, config: dict
) -> str | None:
    """Pick the best instance for a batch task based on fleet state."""
    candidates = [
        i for i in snapshot.instances
        if i.is_healthy
        and (i.admission_can_accept_batch is not False)
    ]
    if not candidates:
        return None

    target_model = config.get("loaded_model_id")

    candidates.sort(
        key=lambda r: (
            # Prefer instance with model already loaded
            target_model not in (r.models_loaded or []) if target_model else False,
            # Prefer instance with more batch headroom
            -(
                (r.admission_batch_max_inflight or r.capacity)
                - r.active_batch
            ),
            # Prefer instance with fewer active RT sessions (less contention)
            r.active_realtime,
        )
    )

    return candidates[0].instance if candidates else None
```

The hint is written into the stream message. The `EngineRunner` checks it:

```python
# In EngineRunner._claim_task():
preferred = message.get("preferred_instance")
if preferred and preferred != self._instance_id:
    # Another instance is preferred — skip and let it claim
    # But only if the message is fresh (< 5s old)
    if message_age < 5.0:
        return None  # Skip, will be reclaimed by preferred or timeout
```

This is a soft hint — if the preferred instance doesn't claim within 5 seconds, any instance picks it up. No task starvation possible.

---

### 79.7: Cross-Mode Metrics

**Files modified:**

- `dalston/orchestrator/metrics.py` — add fleet utilization gauges
- `dalston/common/fleet.py` — add `export_metrics()` method
- `docker/grafana/dashboards/` — add cross-mode utilization dashboard

**Deliverables:**

Prometheus gauges:

```python
# Per engine_id
dalston_fleet_queue_depth{engine_id="faster-whisper"}
dalston_fleet_batch_pressure{engine_id="faster-whisper"}
dalston_fleet_rt_headroom{engine_id="faster-whisper"}
dalston_fleet_total_capacity{engine_id="faster-whisper"}
dalston_fleet_active_batch{engine_id="faster-whisper"}
dalston_fleet_active_rt{engine_id="faster-whisper"}

# Per instance
dalston_instance_active_batch{instance="fw-abc123", engine_id="faster-whisper"}
dalston_instance_active_rt{instance="fw-abc123", engine_id="faster-whisper"}
dalston_instance_admission_can_batch{instance="fw-abc123"}
dalston_instance_admission_can_rt{instance="fw-abc123"}
```

Grafana dashboard with:
- Fleet utilization heatmap (batch vs RT per instance)
- Queue depth over time per engine_id
- Batch pressure ratio with alerting threshold
- RT headroom trend

---

### 79.8: Fleet Status API Endpoint

**Files modified:**

- `dalston/gateway/api/v1/fleet.py` *(new)* — `GET /v1/fleet/status`
- `dalston/gateway/api/v1/__init__.py` — register router

**Deliverables:**

Operator-facing endpoint for fleet visibility:

```json
GET /v1/fleet/status

{
  "engines": {
    "faster-whisper": {
      "instances": 2,
      "healthy_instances": 2,
      "total_capacity": 12,
      "active_batch": 3,
      "active_rt": 2,
      "available": 7,
      "queue_depth": 5,
      "batch_pressure": 0.71,
      "rt_headroom": 4,
      "instances_detail": [
        {
          "instance": "fw-abc123",
          "status": "processing",
          "active_batch": 2,
          "active_rt": 1,
          "capacity": 6,
          "models_loaded": ["large-v3-turbo"],
          "admission": {
            "can_accept_batch": true,
            "can_accept_rt": true,
            "rt_reservation": 2,
            "batch_max_inflight": 4
          }
        }
      ]
    }
  },
  "totals": {
    "total_capacity": 12,
    "active_batch": 3,
    "active_rt": 2,
    "available": 7,
    "total_queue_depth": 5
  }
}
```

---

## Non-Goals

- **Hard preemption of batch tasks for RT** — Too complex and risks data loss. The admission controller's RT reservation already handles this at the engine level. Fleet-level preemption belongs in a separate milestone.
- **Autoscaling integration** — Using fleet signals to trigger scale-up/down events (e.g., `engine.needed` with capacity context). Valuable but separate concern.
- **Per-task priority within batch queue** — Task priority/ordering within a Redis Stream is a different problem. This milestone focuses on cross-mode coordination, not intra-mode scheduling.
- **Dynamic admission tuning** — Automatically adjusting `rt_reservation` / `batch_max_inflight` based on fleet state. Requires careful feedback loop design; better as a follow-up after we have the metrics from 79.7.

---

## Deployment

Steps 79.1–79.3 are purely additive (new fields, new tracking) — safe for rolling deploy.

Steps 79.4–79.5 are observability-first — log new signals but don't change routing. Can be deployed and observed before enabling behavioral changes.

Step 79.6 introduces soft instance hints — backward compatible. Old engines ignore the hint field. New engines respect it but with a 5s timeout fallback.

Steps 79.7–79.8 are metrics/API — no scheduling impact.

**Recommended rollout order:** 79.1 → 79.2 → 79.3 → 79.7 → 79.8 → 79.4 → 79.5 → 79.6

Deploy observability first, validate signals, then enable routing changes.

---

## Verification

```bash
make dev

# 1. Verify admission status in heartbeats
docker compose exec redis redis-cli HGETALL "dalston:engine:instance:faster-whisper-0" \
  | grep -A1 admission

# 2. Verify queue depth tracking
docker compose exec redis redis-cli HGETALL "dalston:fleet:queue_depth"

# 3. Verify fleet status endpoint
curl -s http://localhost:8000/v1/fleet/status \
  -H "Authorization: Bearer $DALSTON_API_KEY" | jq '.engines["faster-whisper"]'

# 4. Submit batch job and verify fleet-aware logging
curl -s -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -F file=@test.wav | jq .job_id

# Check orchestrator logs for fleet state
docker compose logs orchestrator | grep task_queue_fleet_state

# 5. Verify metrics
curl -s http://localhost:9090/api/v1/query?query=dalston_fleet_batch_pressure | jq .
```

---

## Checkpoint

- [ ] Engine heartbeats include admission status fields
- [ ] `EngineRecord` exposes admission fields in registry
- [ ] Queue depth tracker polls stream lengths every 5s
- [ ] `FleetCapacityView` assembles cross-mode snapshots
- [ ] Batch scheduler logs fleet state at queue time
- [ ] Session allocator uses batch load as tiebreaker
- [ ] Batch scheduler emits `preferred_instance` hints
- [ ] Engine runner respects soft instance hints with timeout
- [ ] Prometheus gauges for fleet utilization exported
- [ ] Grafana dashboard for cross-mode utilization
- [ ] `GET /v1/fleet/status` returns fleet snapshot
- [ ] Existing tests pass (`make test`)
- [ ] Mixed-load benchmark (`tests/benchmarks/test_mixed_load.py`) shows no regression
