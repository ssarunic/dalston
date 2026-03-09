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

Batch engines register themselves in Redis on startup, send periodic heartbeats, and auto-expire if they crash. The orchestrator checks engine availability before queuing tasks and fails jobs immediately with a clear error when no healthy engine is available.

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

| Key | Type | Description |
|-----|------|-------------|
| `dalston:batch:engines` | SET | All registered engine_ids |
| `dalston:batch:engine:{engine_id}` | HASH (TTL: 60s) | Engine state |

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

**File:** `dalston/engine_sdk/registry.py`

Created `BatchEngineRegistry` class (mirrors `realtime_sdk/registry.py`) with `register()`, `heartbeat()`, `unregister()`, and `close()` methods. Uses `BatchEngineInfo` dataclass for registration data. Constants: `BATCH_ENGINE_SET_KEY`, `BATCH_ENGINE_KEY_PREFIX`, `BATCH_ENGINE_TTL = 60`.

**Tests:** `tests/unit/test_batch_engine_registry.py` -- registration adds to SET and creates HASH, heartbeat updates fields and refreshes TTL, unregister removes from SET and deletes HASH.

---

### 28.2: Integrate Registry into EngineRunner

**File:** `dalston/engine_sdk/runner.py`

Modified EngineRunner to create a `BatchEngineRegistry` on startup, register the engine with its ID/stage/queue, use registry-based heartbeat calls (reporting idle/processing status and current task ID), and unregister on shutdown. Removed the old direct Redis heartbeat code.

---

### 28.3: Server-Side Registry

**File:** `dalston/orchestrator/registry.py`

Created server-side `BatchEngineRegistry` with query methods: `get_engines()`, `get_engine()`, `get_engines_for_stage()`, `is_engine_available()`. The `BatchEngineState` dataclass includes an `is_available` property that checks heartbeat freshness (< 60s) and status.

**Tests:** `tests/unit/test_orchestrator_registry.py` -- get_engines returns all registered engines, is_engine_available returns False for missing/stale engines, get_engines_for_stage filters correctly.

---

### 28.4: Add EngineUnavailableError

**File:** `dalston/orchestrator/exceptions.py`

Added `EngineUnavailableError` exception with `engine_id` and `stage` attributes. Message format: "Engine '{engine_id}' is not available. No healthy engine registered for stage '{stage}'."

---

### 28.5: Integrate with Scheduler

**File:** `dalston/orchestrator/scheduler.py`

Modified `queue_task()` to accept a `registry` parameter and check `registry.is_engine_available(task.engine_id)` before pushing to the Redis queue. Raises `EngineUnavailableError` if unavailable.

---

### 28.6: Integrate with Handlers

**File:** `dalston/orchestrator/handlers.py`

Modified `handle_job_created` and `handle_task_completed` to accept and pass the registry to `queue_task()`. On `EngineUnavailableError`, the job transitions to FAILED immediately with the error message, and a `transcription.failed` event is published.

---

### 28.7: Integrate with Orchestrator Main

**File:** `dalston/orchestrator/main.py`

Initializes `BatchEngineRegistry` with the Redis connection and passes it through `_dispatch_event` to all handler calls.

---

### 28.8: Legacy Heartbeat Fallback

**Status:** Skipped -- No backwards compatibility needed (dev mode, no live clients).

---

### 28.9: Tests

**Unit tests:**

- `tests/unit/test_batch_engine_registry.py` -- Client-side registry
- `tests/unit/test_orchestrator_registry.py` -- Server-side registry
- `tests/unit/test_scheduler.py` -- Availability check in queue_task

**Integration tests:**

- `tests/integration/test_engine_availability.py`
  - Submit job without engines running -> immediate failure
  - Start engine, submit job -> success
  - Stop engine mid-pipeline -> dependent tasks fail with clear error

---

## Verification

- [ ] Job submitted without engines running fails immediately with clear error message
- [ ] Engines appear in `dalston:batch:engines` SET after startup (within ~10s)
- [ ] Job submitted with engines running completes successfully
- [ ] Engine crash detected within 60s (heartbeat TTL expiry)
- [ ] Stopping an engine mid-pipeline fails dependent tasks with clear error

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

## Unblocked

This milestone enables:

- **AWS deployment** -- Containers can restart without silent failures
- **Spot instance tolerance** -- Engine disappearance is detected and reported
- **Operator visibility** -- Clear errors instead of mysterious timeouts
- **Future work** -- Foundation for dynamic engine routing (M29+)
