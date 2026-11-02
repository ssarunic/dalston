# M105: External-Engine-First Routing

|                    |                                                                                                                       |
| ------------------ | --------------------------------------------------------------------------------------------------------------------- |
| **Goal**           | When a Mac worker is online, auto-routed jobs go to it first; GPU spot capacity is launched only for overflow        |
| **Duration**       | 1–2 days                                                                                                              |
| **Dependencies**   | M91 (Queue-Based GPU Autoscaling), M104 (FluidAudio Mac Worker)                                                       |
| **Deliverable**    | One selector rule, one setting (`DALSTON_EXTERNAL_ENGINE_QUEUE_CAPACITY`), registry backlog helper, unit tests, docs  |
| **Status**         | Implemented — verified locally (capacity 2: overflow at the 3rd task, pinned job bypasses); AWS 21-job spot check pending |

## User Story

> *"As an operator with a Mac worker on my tailnet, I want it to take transcription and diarization work by default whenever it is on, and I want AWS to start spot GPUs only when the Mac cannot keep pace or is off, so that the Mac behaves like one always-on instance in front of the autoscaled fleet."*

---

## Outcomes

The M91 autoscaler sizes the GPU fleet as `ceil(backlog on GPU streams / tasks_per_instance)`, with `tasks_per_instance` = 20 by default. This milestone does not change that rule. It only changes which stream a task is put on, so the autoscaler's existing arithmetic produces the behaviour below.

| Scenario | Current | After M105 |
| -------- | ------- | ---------- |
| Mac online, GPU fleet at zero, `model=auto` job arrives | Mac is the only live engine, job goes to Mac. Same as after, but by accident. | Job goes to Mac because its queue is under capacity. |
| Mac online, a spot worker is already up, `model=auto` job arrives | Both are live; RTF ranking picks the GPU every time. Mac is starved until the GPU idles out. | Mac first while its queue is under capacity. GPU gets only overflow and idles out after cooldown. |
| Mac online with 20 tasks queued, 21st task arrives | Goes to Mac. Queue grows without bound; spot never launches. | Goes to the GPU stream. Autoscaler sees backlog 1 and launches the first spot. 41st task launches the second. |
| Mac offline | On-demand catalog fallback puts the task on the GPU stream; spot launches at the first task. | Unchanged. |
| Job pins a `fluidaudio/*` model | Routed to Mac, or `runtime_unavailable` if it is off. | Unchanged. The rule only applies to auto-routing. |
| Job pins a GPU model | Mac ignored. | Unchanged. |

Resulting fleet sizes with `tasks_per_instance` = 20 and capacity = 20:

| Mac | Tasks queued in total | On the Mac | On GPU streams | Spot instances |
|---|---|---|---|---|
| on | 1–20 | 1–20 | 0 | 0 |
| on | 21–40 | 20 | 1–20 | 1 |
| on | 41–60 | 20 | 21–40 | 2 |
| off | 1–20 | 0 | 1–20 | 1 |
| off | 21–40 | 0 | 21–40 | 2 |

"The Mac is one always-on instance" is exact: it absorbs one instance's worth of queue before the GPU streams see anything, and the autoscaler counts from there.

---

## Motivation

M104 added `deployment: external` engines that heartbeat into the registry like any other worker but cannot be launched by the control plane. M91's autoscaler watches only its own shape's streams (`nemo`, `pyannote-4.0`). The two never interact directly, which is correct, but the selector's "any live engine beats the on-demand fallback" rule has two consequences the operator did not ask for:

1. While the Mac is online, auto-routed work never reaches a GPU stream, so spot never launches no matter how deep the Mac's queue gets. A single Mac at `max_concurrency: 1` silently becomes the ceiling for all batch throughput.
2. While a spot worker is live, RTF ranking sends everything to it and the Mac sits idle, which defeats the point of attaching it.

One rule fixes both: prefer the external engine while its queue is under a capacity, and otherwise behave as if it were not there.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  select_engine(stage, requirements, ...)          (dalston/orchestrator/      │
│                                                    engine_selector.py)        │
│                                                                               │
│  1. user pinned a model/engine ───────────────▶ unchanged, rule not applied   │
│  2. capable = live, healthy engines for stage                                 │
│                                                                               │
│  3. NEW  external = [e in capable if catalog(e).deployment == "external"]     │
│          for each external e:                                                 │
│              backlog = registry.get_stream_backlog(e.stream_name)             │
│              if backlog < settings.external_engine_queue_capacity:            │
│                  return e        ◀── "external engine under capacity (n/20)"  │
│          capable -= external     ◀── "external engine at capacity, overflow"  │
│                                                                               │
│  4. rank remaining live engines by RTF (unchanged)                            │
│  5. none live → on_demand catalog fallback (unchanged) ──▶ GPU stream         │
│                                                        └──▶ M91 autoscaler    │
│                                                             launches spot     │
└──────────────────────────────────────────────────────────────────────────────┘
```

`backlog` is the engine stream's undelivered count (XINFO GROUPS `lag`) plus its in-flight count (XPENDING), the same reading the M91 autoscaler uses for GPU streams. An in-flight task still occupies the Mac, so it counts.

The rule runs before ranking, which is what keeps the Mac first even when a GPU worker is live. Each external engine is judged on its own stream, so the transcribe and diarize adapters each get their own capacity, and a second Mac would simply add another engine with its own queue.

---

## Steps

### 105.1: Backlog reader on the registry

**Files modified:**

- `dalston/common/registry.py` — add `UnifiedEngineRegistry.get_stream_backlog(stream_key) -> int`; `heartbeat()` (async and sync) also carries `stream_name`, since a key the heartbeat re-creates after TTL expiry otherwise loses it (M101 covered the other identifying fields but not this one)
- `dalston/engine_sdk/runner.py` — heartbeat passes `stream_name=self.stream_key`
- `dalston/gateway/api/console.py` — `_get_stream_backlog` calls the new method instead of its private copy

**Deliverables:**

```python
async def get_stream_backlog(self, stream_key: str) -> int:
    """Undelivered (group lag) + in-flight (PEL) tasks on an engine stream.

    Missing stream or group means nothing was ever enqueued: 0. A nil lag
    (entries after last-delivered-id were XDELed) falls back to counting
    the entries that still exist after last-delivered-id, as the M91
    autoscaler does, so a cancelled job cannot read as an empty queue.
    """
```

Returns `lag + pending` for the `engines` consumer group on `dalston:stream:{engine_id}`. The console queue board keeps showing lag only; it passes through the same helper and subtracts nothing, because the board's semantics are "waiting", not "occupying". Keep that distinction in the docstring so nobody unifies them later.

---

### 105.2: The selector rule and its setting

**Files modified:**

- `dalston/config.py` — new field
- `dalston/orchestrator/engine_selector.py` — rule in `select_engine` between the capability filter and `_rank_capable_engines`; `selection_reason` strings
- `docs/plan/milestones/M91-queue-based-gpu-autoscaling.md` — one paragraph under the policy section pointing here

**Deliverables:**

```python
# dalston/config.py
external_engine_queue_capacity: int = Field(
    default=20,
    ge=1,
    description=(
        "Tasks an external engine (deployment: external, e.g. the M104 Mac "
        "worker) may have queued or in flight before auto-routing overflows "
        "to other engines. Keep equal to the autoscale policy's "
        "tasks_per_instance so the external engine counts as exactly one "
        "instance in front of the GPU fleet."
    ),
)
```

Environment variable: `DALSTON_EXTERNAL_ENGINE_QUEUE_CAPACITY`.

In `select_engine`, after `capable` is computed and before ranking:

```python
external = [e for e in capable if catalog.is_external(e.engine_id)]
for engine in external:
    backlog = await registry.get_stream_backlog(engine.stream_name)
    if backlog < settings.external_engine_queue_capacity:
        return EngineSelectionResult(
            engine_id=engine.engine_id,
            ...,
            selection_reason=f"external engine under capacity ({backlog}/{capacity})",
        )
    logger.info(
        "external_engine_overflow",
        engine_id=engine.engine_id,
        stage=stage,
        backlog=backlog,
        capacity=capacity,
    )
capable = [e for e in capable if e not in external]
# existing ranking and on_demand fallback follow unchanged
```

`_is_external(catalog, engine_id)` is a one-line helper over the catalog entry's existing `deployment` field. The stream key is `record.stream_name`, falling back to `dalston:stream:{engine_id}` so a record missing the field can never read as an empty queue. When several external engines are capable, the existing `_rank_capable_engines` ordering decides which is tried first; the first under capacity wins.

The rule is skipped entirely when the user pinned a model or engine, because those paths return before this point today.

---

### 105.3: Tests

**Files modified:**

- `tests/unit/test_engine_selector.py` — new cases
- `tests/unit/test_registry.py` — `get_stream_backlog` against a fake Redis

**Deliverables:**

Unit cases, each with a fake registry whose `get_stream_backlog` is scripted and a catalog with one external and one `on_demand` compose engine:

- external live, backlog 0, GPU scaled to zero: external selected, reason "under capacity".
- external live, backlog 0, GPU live: external selected (not the faster GPU).
- external live, backlog 20, GPU scaled to zero: on_demand fallback selected, `external_engine_overflow` logged.
- external live, backlog 20, GPU live: GPU selected.
- external offline: unchanged behaviour, no backlog read.
- model pinned to the external engine with backlog 50: external selected, no backlog read.
- capacity setting 1: second task overflows.
- `get_stream_backlog`: missing stream → 0; lag nil with surviving entries → counts them; lag + pending summed.

---

## Non-Goals

- **Re-routing tasks already on the Mac's stream when it goes offline** — a task accepted while the Mac was on waits for it or times out. Needs the reconciler to re-select an engine for undelivered tasks of a dead external engine; separate milestone.
- **Threshold in audio minutes or estimated wait** — tasks are the unit the autoscaler uses; matching it matters more than being right about long files. Revisit if long recordings dominate.
- **Counting tasks that are selected but not yet on the stream** — engines are chosen at submission, and a transcribe or diarize task only reaches the external engine's stream once prepare finishes. A burst of uploads inside that prepare latency (about a second) all read an empty stream and all land on the Mac, so the capacity is exceeded by the burst size, not by sustained load. The autoscaler has the same blind spot. Counting assigned-but-unqueued tasks from the task table would close it; separate change if bursts turn out to matter.
- **Hysteresis** — the decision is per task and the autoscaler's cooldown already damps the GPU side.
- **Autoscaler changes** — none. The policy's `tasks_per_instance` and this capacity are two settings that should be set to the same number; unifying them would couple the control plane to the AWS policy file.
- **Realtime sessions** — external engines are batch-only (M104).
- **Preferring the Mac for pinned GPU models** — pinned means pinned.
- **Measured FluidAudio RTFs** — M104.5; irrelevant here because the rule runs before RTF ranking.

---

## Deployment

Standard rolling deploy of the orchestrator. No migration.

Operators must set `DALSTON_EXTERNAL_ENGINE_QUEUE_CAPACITY` to the same value as `tasks_per_instance` in `/data/dalston/autoscale-policy.yaml` on the control plane (both default to 20). `docker-compose.yml` forwards the variable into the orchestrator through the `core-env` anchor, so setting it in the compose environment file is enough; the AWS override inherits it. The `fluidaudio/*` model rows must be `ready` on the control plane for `model=auto` to resolve to the Mac (M104 pull step).

---

## Verification

```bash
# Local stack with the Mac adapters running against it (M104 guide), capacity 2 for a quick test
export DALSTON_EXTERNAL_ENGINE_QUEUE_CAPACITY=2
make dev-minimal

# Pause the transcribe adapter so its queue builds (SIGSTOP keeps the heartbeat thread alive
# for 60 s, long enough for the next steps)
pkill -STOP -f "stt-transcribe/fluidaudio/engine.py"

# Submit four auto-routed jobs, spaced so each prepare lands its task on the
# stream before the next selection reads the backlog (see Non-Goals on bursts)
for i in 1 2 3 4; do
  curl -s -X POST http://localhost:8000/v1/audio/transcriptions \
    -H "Authorization: Bearer $DALSTON_API_KEY" \
    -F file=@meeting.wav -F model=auto | jq -r .id
  sleep 4
done

# Expect: jobs 1–2 on dalston:stream:fluidaudio, jobs 3–4 on the fallback engine's stream
docker compose exec redis redis-cli XINFO GROUPS dalston:stream:fluidaudio | grep -A1 lag
docker compose logs orchestrator | grep -E "external engine (under|at) capacity|external_engine_overflow"

pkill -CONT -f "stt-transcribe/fluidaudio/engine.py"

# Pinned jobs ignore the rule: with the adapter still paused and queue at capacity, this still lands on the Mac
curl -s -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -F file=@meeting.wav -F model=fluidaudio/parakeet-tdt-0.6b-v3 | jq -r .id
```

On AWS, with the Mac attached and capacity 20: submit 21 `model=auto` jobs and confirm one spot launch in the autoscale audit log; submit 21 more and confirm a second.

---

## Checkpoint

- [x] `UnifiedEngineRegistry.get_stream_backlog` returns lag + pending, handles missing stream and nil lag; console board uses it
- [x] `DALSTON_EXTERNAL_ENGINE_QUEUE_CAPACITY` validated at startup, default 20
- [x] `select_engine` prefers a capable external engine under capacity even when a faster GPU engine is live
- [x] At capacity, the external engine is excluded and the existing ranking / on_demand fallback runs; `external_engine_overflow` logged
- [x] Pinned model and pinned engine paths unchanged and untested for backlog
- [x] Unit tests in 105.3 pass; `make lint` clean
- [x] Local verification block passes with capacity 2
- [x] M91 spec cross-references M105; operator guide for the Mac worker mentions the setting
- [ ] AWS: 21 auto-routed jobs with the Mac attached produce exactly one spot launch in the autoscale audit log
