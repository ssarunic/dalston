# M80: Engine Control Plane (Push-Based Unified Dispatch)

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | Replace the dual dispatch model (pull-from-stream for batch, push-via-WS for realtime) with a single push-based control plane where the orchestrator places all work on engine instances |
| **Duration**       | 3–4 weeks                                                    |
| **Dependencies**   | M63 (Engine Unification), M64 (Registry Unification), M66 (Session Router Consolidation) |
| **Deliverable**    | Engine control API, orchestrator-driven placement for both modes, fleet status API, deprecation of stream-pull dispatch |
| **Status**         | Not Started                                                  |

## User Story

> *"As a platform operator, I want the orchestrator to decide exactly which engine instance handles each piece of work — batch or realtime — so that GPU utilization is globally optimal and I have one place to observe and control scheduling."*

---

## Motivation

Dalston currently has two fundamentally different dispatch paths:

```
Batch:     Orchestrator → Redis Stream → Engine PULLS task (poll loop)
Realtime:  Gateway → Orchestrator allocates → Client WS pushed to Engine
```

This split causes real problems:

1. **Orchestrator is blind to batch execution.** It pushes a task ID into a Redis Stream and hopes an engine picks it up. It doesn't know which instance claimed it, how long it's been waiting, or whether the instance it hoped for is actually the one processing it.

2. **No instance-level batch placement.** The stream is keyed by `engine_id`, not by instance. All instances of the same engine_id compete in a consumer group. The orchestrator can't direct a task to the instance with the right model loaded or the most batch headroom.

3. **Admission control is split-brain.** The per-engine `AdmissionController` enforces QoS locally (RT reservation, batch cap), but the orchestrator can't see or coordinate with it. It queues batch tasks to an engine that will NACK them, creating pointless redelivery cycles.

4. **Two codepaths to maintain.** The `EngineRunner` has ~500 lines of stream polling, stale claiming, consumer group management, and deferred-task recovery. The realtime path has its own allocation and WS proxy logic. Both need independent testing, monitoring, and failure handling.

5. **The "fleet scheduler" problem (M79) is a symptom.** M79 proposed adding queue depth tracking, admission status in heartbeats, and instance hints — all to compensate for the orchestrator's blindness. A push model eliminates the need for most of that machinery because the orchestrator *is* the scheduler.

### How NIMs do it

NVIDIA NIMs expose a stateless inference API (gRPC/REST). The orchestration layer pushes requests to specific instances. The NIM doesn't poll a queue — it receives work, processes it, returns results. The control plane (Triton, k8s, or custom) makes all placement decisions.

Dalston engines are heavier than NIMs (model management, S3 I/O, multi-minute processing), but the dispatch model can be the same: orchestrator pushes, engine receives.

### Why Redis stays

Redis is not going away. It remains the orchestrator's backbone for:

- **Job/task state** — status, metadata, DAG progress
- **Durable events** — `task.started`, `task.completed`, pub/sub notifications
- **Heartbeats** — engine registration and health (optional: engines can also report via control API)
- **Session state** — realtime session tracking
- **Coordination** — leader election, distributed locks

What changes: Redis Streams stop being the batch dispatch mechanism. The orchestrator pushes work directly to engines via their control API.

---

## Architecture

### Current (dual dispatch)

```
┌──────────────┐    task_id     ┌───────────────┐    XREADGROUP    ┌──────────────┐
│ Orchestrator │ ──────────────→│ Redis Stream   │←────────────────│ Engine       │
│              │                │ (per engine_id)│                  │ (poll loop)  │
└──────────────┘                └───────────────┘                  └──────────────┘

┌──────────────┐   allocate     ┌───────────────┐    WS connect    ┌──────────────┐
│ Gateway      │ ──────────────→│ Orchestrator   │                  │ RT Engine    │
│ (WS proxy)   │                │ (allocator)   │                  │ (WS server)  │
└──────────────┘                └───────────────┘                  └──────────────┘
```

### After M80 (unified push)

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           ORCHESTRATOR                                    │
│                                                                          │
│   ┌──────────────┐    ┌──────────────┐    ┌──────────────────────────┐   │
│   │ DAG Scheduler│    │ Fleet Placer │    │ Instance Tracker          │   │
│   │              │───→│              │───→│ (capacity, models, health)│   │
│   │ "task ready" │    │ "place on    │    │                          │   │
│   │              │    │  instance X" │    │ Source: heartbeats +      │   │
│   └──────────────┘    └──────┬───────┘    │ control API responses    │   │
│                              │            └──────────────────────────┘   │
│                              │                                           │
└──────────────────────────────┼───────────────────────────────────────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
         POST /task       POST /task       POST /session
              │                │                │
     ┌────────▼───┐   ┌───────▼────┐   ┌───────▼────┐
     │ Engine A   │   │ Engine B   │   │ Engine C   │
     │ (batch)    │   │ (unified)  │   │ (unified)  │
     │            │   │            │   │            │
     │ :9100      │   │ :9100      │   │ :9100      │
     │ /task      │   │ /task      │   │ /task      │
     │ /health    │   │ /session   │   │ /session   │
     │ /status    │   │ /health    │   │ /health    │
     │ /cancel    │   │ /status    │   │ /status    │
     └────────────┘   │ /cancel    │   │ /cancel    │
                      └────────────┘   └────────────┘
```

Key change: The orchestrator calls the engine, not the other way around.

---

## Steps

### 80.1: Engine Control API — Specification & Stub

**Files modified:**

- `dalston/engine_sdk/control_api.py` *(new)* — HTTP control API server (extends existing metrics server)
- `dalston/engine_sdk/control_types.py` *(new)* — Request/response types for control endpoints

**Deliverables:**

Define the control plane interface that engines expose. This is a small HTTP API on the existing metrics port (9100), not a new service.

```python
# Control API endpoints (all on :9100)

# Existing
GET  /health          → {"status": "healthy", ...}
GET  /metrics         → Prometheus format

# New — batch task dispatch
POST /task            → Accept a batch task for processing
     Request:  {"task_id": str, "job_id": str, "stage": str,
                "config": dict, "timeout_seconds": int,
                "artifacts": list[ArtifactRef]}
     Response: 202 Accepted {"task_id": str}
               409 Conflict  {"error": "at_capacity"}  (admission rejected)
               503 Unavailable {"error": "draining"}

# New — batch task cancellation
DELETE /task/{task_id} → Cancel an in-progress task
     Response: 200 OK / 404 Not Found

# New — instance status (richer than /health)
GET  /status          → Full instance state
     Response: {"instance": str, "engine_id": str,
                "active_batch": [...task_ids],
                "active_rt": [...session_ids],
                "capacity": int, "available": int,
                "admission": {...},
                "models_loaded": [...],
                "gpu_memory_used_mb": int}

# Existing (realtime — unchanged)
WS   /session         → WebSocket realtime session (existing protocol)
```

Why HTTP and not gRPC:
- Engines already run an HTTP server on 9100 for metrics/health
- No new dependency (no protobuf compiler, no gRPC runtime in every engine image)
- HTTP is debuggable (`curl`), observable (access logs), and cacheable
- Latency is irrelevant — we're dispatching multi-second/minute batch tasks, not sub-ms inference
- For realtime, the existing WebSocket protocol stays unchanged (already push-based)

This step: implement the API server skeleton + types. The endpoints return stubs (202 for /task, status for /status). Wire it into the existing metrics HTTP server.

---

### 80.2: Engine Control API — Task Processing

**Files modified:**

- `dalston/engine_sdk/control_api.py` — implement `POST /task` handler
- `dalston/engine_sdk/runner.py` — extract task processing into `_process_task()` method usable by both poll and push paths
- `dalston/engine_sdk/admission.py` — no change (already used by control API)

**Deliverables:**

The `POST /task` handler:

1. Checks admission (`can_accept_batch()`) → 409 if rejected
2. Admits the task (`admit_batch()`)
3. Spawns processing in a thread (existing `_process_task` logic)
4. Returns 202 immediately (async processing)
5. On completion/failure: publishes durable events to Redis (same as today)
6. On completion: calls `release_batch()`

```python
class ControlAPI:
    """HTTP control plane for engine instances."""

    def __init__(
        self,
        engine: Engine,
        admission: AdmissionController,
        runner: EngineRunner,
    ):
        self._engine = engine
        self._admission = admission
        self._runner = runner
        self._active_tasks: dict[str, Future] = {}

    def handle_post_task(self, request: TaskAssignment) -> Response:
        if not self._admission.admit_batch():
            return Response(409, {"error": "at_capacity"})

        future = self._runner.submit_task(request)
        self._active_tasks[request.task_id] = future
        return Response(202, {"task_id": request.task_id})

    def handle_delete_task(self, task_id: str) -> Response:
        future = self._active_tasks.get(task_id)
        if not future:
            return Response(404, {"error": "not_found"})
        future.cancel()
        return Response(200, {"task_id": task_id})
```

The key refactor: extract the guts of `EngineRunner._process_one_task()` (S3 download, `engine.process()`, S3 upload, event publish) into a reusable `submit_task()` that works for both push and pull paths.

---

### 80.3: Orchestrator — Direct Instance Placement

**Files modified:**

- `dalston/orchestrator/placer.py` *(new)* — `FleetPlacer` that selects instance + pushes task
- `dalston/orchestrator/scheduler.py` — add push-based dispatch path alongside existing stream path
- `dalston/common/registry.py` — add `endpoint` field to `EngineRecord` (control API URL)

**Deliverables:**

```python
class FleetPlacer:
    """Places batch tasks on specific engine instances via control API."""

    def __init__(self, registry: UnifiedEngineRegistry, http: httpx.AsyncClient):
        self._registry = registry
        self._http = http

    async def place_task(self, task: Task, config: dict) -> PlacementResult:
        """Select best instance and push task to it."""
        candidates = await self._registry.get_available(
            interface="batch",
            engine_id=task.engine_id,
        )
        if not candidates:
            return PlacementResult(placed=False, reason="no_healthy_instance")

        # Rank by: model warmth → batch headroom → RT pressure
        target_model = config.get("loaded_model_id")
        candidates.sort(
            key=lambda r: (
                target_model not in (r.models_loaded or []) if target_model else False,
                -r.available_capacity,
                r.active_realtime,  # prefer less RT contention
            )
        )

        # Try candidates in ranked order
        for instance in candidates:
            try:
                resp = await self._http.post(
                    f"{instance.endpoint}/task",
                    json={
                        "task_id": task.id,
                        "job_id": task.job_id,
                        "stage": task.stage,
                        "config": config,
                        "timeout_seconds": task.timeout_seconds,
                    },
                    timeout=5.0,
                )
                if resp.status_code == 202:
                    return PlacementResult(
                        placed=True,
                        instance=instance.instance,
                    )
                if resp.status_code == 409:
                    continue  # At capacity, try next
                # 503 draining, also try next
            except httpx.RequestError:
                continue  # Instance unreachable, try next

        return PlacementResult(placed=False, reason="all_instances_rejected")
```

The scheduler gains a dispatch mode flag:

```python
class Scheduler:
    def __init__(self, ..., dispatch_mode: str = "stream"):
        # "stream" = existing pull-based (default, backward-compatible)
        # "push"   = new control API placement
        self._dispatch_mode = dispatch_mode

    async def queue_task(self, task, config, ...):
        if self._dispatch_mode == "push":
            result = await self._placer.place_task(task, config)
            if not result.placed:
                # Fallback: queue to stream (degraded mode)
                logger.warning("push_placement_failed_falling_back",
                               reason=result.reason)
                await self._queue_to_stream(task, config)
            return
        # Existing stream path
        await self._queue_to_stream(task, config)
```

This step keeps stream dispatch as fallback. Engines that haven't upgraded to the control API still work via the existing pull path.

---

### 80.4: Engine Registration with Control Endpoint

**Files modified:**

- `dalston/engine_sdk/runner.py` — include control API endpoint in registration
- `dalston/realtime_sdk/base.py` — same for RT engines
- `dalston/common/registry.py` — persist and expose `endpoint` field

**Deliverables:**

When an engine registers, it now includes its control API URL:

```python
# In EngineRunner._register():
record = EngineRecord(
    instance=self.instance,
    engine_id=self.engine_id,
    # ... existing fields ...
    endpoint=f"http://{hostname}:{self.metrics_port}",  # control API
)
```

The `endpoint` field is what `FleetPlacer` uses to reach the engine. For Docker Compose, this is the container hostname. For k8s, it's the pod IP. For local dev, it's `localhost:{port}`.

The registry query `get_available()` now includes the endpoint so the placer can reach instances directly.

---

### 80.5: Realtime Session Placement via Control Plane

**Files modified:**

- `dalston/orchestrator/session_allocator.py` — use control API `/status` for richer allocation decisions
- `dalston/engine_sdk/control_api.py` — implement `GET /status` fully

**Deliverables:**

Today, the session allocator reads capacity from the registry (populated by heartbeats every 10s). With the control API, it can query an engine's live status before allocating:

```python
async def acquire_worker(self, ...):
    candidates = await self._registry.get_available(
        interface="realtime", engine_id=engine_id, model=model,
    )

    # Pre-check: query live status for top candidates
    # (skip if >5 candidates — heartbeat data is good enough for rough filtering)
    if len(candidates) <= 5:
        for c in candidates:
            try:
                resp = await self._http.get(
                    f"{c.endpoint}/status", timeout=1.0,
                )
                if resp.status_code == 200:
                    live = resp.json()
                    c._live_available = live["available"]
                    c._live_batch_count = len(live.get("active_batch", []))
            except httpx.RequestError:
                c._live_available = c.available_capacity  # Fallback to heartbeat

    # Sort with live data
    candidates.sort(
        key=lambda r: (
            model not in (r.models_loaded or []),
            -(getattr(r, "_live_available", r.available_capacity)),
            getattr(r, "_live_batch_count", r.active_batch),
        )
    )
    # ... existing atomic allocation ...
```

This is optional and additive. If the status query fails or times out (1s), the allocator falls back to heartbeat data. No degradation.

---

### 80.6: Orchestrator Task Tracking (Push Mode)

**Files modified:**

- `dalston/orchestrator/placer.py` — add placement tracking
- `dalston/orchestrator/handlers.py` — handle push-mode task lifecycle

**Deliverables:**

In pull mode, the orchestrator learns about task progress via durable events (`task.started`, `task.completed`). This doesn't change in push mode — engines still publish these events.

What changes: the orchestrator now tracks *placement* state:

```python
# Redis hash: dalston:placement:{task_id}
{
    "instance": "fw-abc123",       # Which instance accepted the task
    "placed_at": "2026-03-17T...", # When it was placed
    "status": "placed",            # placed | processing | completed | failed
}
```

This enables:
- **Placement timeout detection**: If an engine accepts a task (202) but never emits `task.started` within N seconds, the orchestrator can re-place it.
- **Instance failure recovery**: If the placed instance goes offline (heartbeat expires), the orchestrator can re-place its tasks immediately — not wait for stream stale-claiming.
- **Observability**: "Task X was placed on instance Y at time Z" — direct visibility the stream model never had.

---

### 80.7: Deprecate Stream Polling (Feature Flag)

**Files modified:**

- `dalston/engine_sdk/runner.py` — make stream polling conditional on `DALSTON_DISPATCH_MODE`
- `dalston/orchestrator/scheduler.py` — make dispatch mode configurable

**Deliverables:**

Environment variable `DALSTON_DISPATCH_MODE` controls the dispatch path:

| Value | Orchestrator | Engine |
|-------|-------------|--------|
| `stream` (default) | Pushes to Redis Stream | Polls Redis Stream |
| `push` | Calls engine control API, falls back to stream | Accepts via control API, also polls stream (hybrid) |
| `push_only` | Calls engine control API only, no stream fallback | Accepts via control API only, no stream polling |

Migration path:
1. Deploy engines with control API (80.1–80.2) — they still poll streams
2. Switch orchestrator to `push` mode — tasks go via control API, stream is fallback
3. Observe for a deployment cycle — verify no tasks use stream fallback
4. Switch to `push_only` — stream polling stops, EngineRunner poll loop disabled

The stream poll loop in `EngineRunner.run()` becomes:

```python
def run(self) -> None:
    self._running = True
    self._setup_signal_handlers()
    self._start_metrics_server()  # Now includes control API
    self._register()
    self._start_heartbeat()

    dispatch_mode = os.environ.get("DALSTON_DISPATCH_MODE", "stream")

    if dispatch_mode in ("stream", "push"):
        # Stream polling — existing or hybrid mode
        self._poll_loop()
    else:
        # push_only — just keep alive for control API
        logger.info("engine_push_only_mode", instance=self.instance)
        self._wait_for_shutdown()
```

---

### 80.8: Fleet Status API & Metrics

**Files modified:**

- `dalston/gateway/api/v1/fleet.py` *(new)* — `GET /v1/fleet/status`
- `dalston/gateway/api/v1/__init__.py` — register router
- `dalston/orchestrator/metrics.py` — Prometheus gauges for placement

**Deliverables:**

Fleet status endpoint (same as M79.8, but now includes placement data):

```json
GET /v1/fleet/status
{
  "dispatch_mode": "push",
  "engines": {
    "faster-whisper": {
      "instances": 2,
      "healthy_instances": 2,
      "total_capacity": 12,
      "active_batch": 3,
      "active_rt": 2,
      "available": 7,
      "pending_placements": 1,
      "instances_detail": [
        {
          "instance": "fw-abc123",
          "endpoint": "http://fw-abc123:9100",
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
  }
}
```

Prometheus metrics:

```
dalston_placement_total{engine_id, outcome="accepted|rejected|failed"}
dalston_placement_latency_seconds{engine_id}
dalston_placement_fallback_total{engine_id}  # stream fallback count
dalston_fleet_dispatch_mode{mode="stream|push|push_only"}
```

---

## Non-Goals

- **gRPC control plane** — HTTP is sufficient for the dispatch latencies involved (seconds-to-minutes batch tasks). gRPC adds protobuf compilation to every engine build. If sub-millisecond dispatch matters later, it can be added as an alternative transport behind the same interface.
- **Removing Redis entirely** — Redis remains for job state, events, heartbeats, and session tracking. Only the stream-pull dispatch path is replaced.
- **Changing the realtime WebSocket protocol** — The client-to-engine WS protocol is unchanged. What changes is how the orchestrator *places* the session (richer status queries), not the session itself.
- **Multi-orchestrator placement coordination** — This milestone assumes a single orchestrator (or leader-elected). Sharded placement across multiple orchestrators is a separate concern.
- **Engine autoscaling** — Using placement rejection rates to trigger scale-up. Valuable, but separate milestone.

---

## Risks

### Orchestrator becomes a single point of failure for dispatch

**Mitigation:**
- In `push` mode (not `push_only`), stream fallback ensures tasks are still dispatched if orchestrator restarts
- Engine heartbeats continue independently of the control API
- Orchestrator restart recovery: re-place tasks that were in `placed` state for stale instances
- Long-term: orchestrator HA via leader election (existing pattern)

### Engine control API adds latency to dispatch

**Mitigation:**
- The HTTP call to the engine (~1-5ms on local network) replaces a Redis Stream push (~1ms) + poll timeout (up to 30s). Net effect: dispatch is *faster* in push mode.
- Timeout on placement calls (5s) prevents blocking on unreachable instances.

### Network partition between orchestrator and engine

**Mitigation:**
- Placement timeout + retry to next instance
- Engine publishes durable events to Redis (independent of control API)
- Heartbeat-based health detection catches persistent partitions

---

## Deployment

**Rollout strategy (zero-downtime):**

1. Deploy engines with control API (80.1, 80.2, 80.4) — engines still poll streams, control API is bonus
2. Deploy orchestrator with `DALSTON_DISPATCH_MODE=push` (80.3, 80.6) — push with stream fallback
3. Monitor: check `dalston_placement_fallback_total` — should be near zero
4. Deploy orchestrator with `DALSTON_DISPATCH_MODE=push_only` (80.7) — stream polling disabled
5. Deploy fleet API + metrics (80.8) — observability

Each step is independently deployable. Rollback at any step: set `DALSTON_DISPATCH_MODE=stream`.

---

## Verification

```bash
make dev

# 1. Verify control API is running on engine
curl -s http://localhost:9100/status | jq .

# 2. Push a task directly to engine (manual test)
curl -s -X POST http://localhost:9100/task \
  -H "Content-Type: application/json" \
  -d '{"task_id": "test-1", "job_id": "test-job-1", "stage": "transcribe",
       "config": {"loaded_model_id": "large-v3-turbo"}, "timeout_seconds": 300}' \
  | jq .

# 3. Submit job via API and verify push placement
export DALSTON_DISPATCH_MODE=push
curl -s -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -F file=@test.wav | jq .job_id

# Check orchestrator logs for placement
docker compose logs orchestrator | grep "task_placed"

# 4. Verify fleet status
curl -s http://localhost:8000/v1/fleet/status \
  -H "Authorization: Bearer $DALSTON_API_KEY" | jq .

# 5. Verify no stream fallback
curl -s http://localhost:9090/api/v1/query?query=dalston_placement_fallback_total | jq .
```

---

## Checkpoint

- [ ] Engine control API serves `/task`, `/status`, `/cancel` on :9100
- [ ] `POST /task` checks admission and spawns async processing
- [ ] `DELETE /task/{id}` cancels in-progress tasks
- [ ] `GET /status` returns live instance state
- [ ] `FleetPlacer` selects instance and pushes via control API
- [ ] Orchestrator supports `DALSTON_DISPATCH_MODE=push` with stream fallback
- [ ] Engine registration includes `endpoint` field
- [ ] Placement state tracked in Redis (`dalston:placement:{task_id}`)
- [ ] Session allocator queries live `/status` for top candidates
- [ ] `DALSTON_DISPATCH_MODE=push_only` disables stream polling
- [ ] `GET /v1/fleet/status` returns fleet snapshot with placement data
- [ ] Prometheus metrics for placement outcomes and latency
- [ ] Existing tests pass (`make test`)
- [ ] Mixed-load benchmark shows no regression
- [ ] Stream fallback metric is zero in `push` mode before switching to `push_only`
