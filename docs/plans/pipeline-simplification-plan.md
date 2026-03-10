# Architecture Simplification: Implementation Plan

## Scope Decisions

This plan is split into two cycles to bound migration risk:

**Cycle 1 (this iteration):** Unify existing batch+realtime engines and
coordination surfaces. Keep current pipeline behavior (DAG, align,
per_channel, merge) unchanged. Ship PII post-processing behind a feature
flag.

**Cycle 2 (next iteration):** Replace DAG with linear pipeline, eliminate
merge engine, collapse align into transcribe, rearchitect per-channel as
pre-processing split, introduce declarative engine.yaml.

This ordering is deliberate: the pipeline refactor touches every engine's
output contract. Doing it while the engine layer is also being refactored
doubles the blast radius. Stabilize the engine layer first, then reshape
the pipeline on top of it.

## Guiding Principles

- **One logical change per step.** Each step is a single commit.
- **Tests first.** Add characterization tests before refactoring.
- **`make test && make lint` after every commit.** Red = stop and fix.
- **Backward-compatible API output.** Gateway consumers see identical JSON.
- **No big bang.** Old and new code coexist during transition.
- **Feature flags for behavioral changes.** Especially PII post-processing.

---

# Cycle 1: Engine Unification + Coordination Consolidation

## PR-1: Shared Faster-Whisper Transcribe Core

*Extract common inference/model-loading from duplicate batch+RT engines
into a shared core. Preserve both I/O adapters and output contracts.*

### Phase 1.0: Safety Net

#### Step 1.0.1 — Contract tests for batch faster-whisper engine

Add `tests/unit/test_faster_whisper_batch_contract.py`:

- Test `process()` with representative audio fixtures
- Assert output shape: `TranscribeOutput` with segments, text, language
- Assert word timestamp behavior with/without alignment
- Snapshot output for regression comparison

**Verify:** `make test` passes.

#### Step 1.0.2 — Contract tests for RT faster-whisper engine

Add `tests/unit/test_faster_whisper_rt_contract.py`:

- Test `process_chunk()` with streaming audio fixtures
- Assert chunk result shape and streaming behavior
- Assert session lifecycle (start, chunks, finalize)

**Verify:** `make test` passes.

### Phase 1.1: Extract Shared Core

#### Step 1.1.1 — Create `TranscribeCore` shared inference module

Extract common model loading and transcription logic into a new module
shared by both engines:

```python
# engines/stt-transcribe/faster-whisper/core.py
class TranscribeCore:
    """Shared inference logic for faster-whisper batch and realtime."""

    def load(self, model_id: str, device: str, compute_type: str) -> None:
        """Load faster-whisper model."""
        ...

    def transcribe(self, audio: np.ndarray, config: dict) -> dict:
        """Run inference. Returns raw transcription result."""
        ...

    def transcribe_stream(self, audio_chunk: np.ndarray, config: dict) -> dict:
        """Process a streaming chunk. Returns partial result."""
        ...
```

This is a new file alongside existing engines. Nothing imports it yet.

**Verify:** `make test` passes.

#### Step 1.1.2 — Batch engine delegates to `TranscribeCore`

Update `engines/stt-transcribe/faster-whisper/engine.py` to:
1. Instantiate `TranscribeCore` in `__init__`
2. Delegate `process()` to `core.transcribe()`
3. Keep all output formatting and `EngineOutput` construction in the engine

**Verify:** `make test` passes. Contract tests from 1.0.1 produce
identical output.

#### Step 1.1.3 — RT engine delegates to `TranscribeCore`

Update `engines/stt-rt/faster-whisper/engine.py` to:
1. Instantiate `TranscribeCore` in `__init__`
2. Delegate `process_chunk()` to `core.transcribe_stream()`
3. Keep all session management and WebSocket protocol in the RT engine

**Verify:** `make test` passes. Contract tests from 1.0.2 pass.

#### Step 1.1.4 — Single container serves both adapters

Create a unified entry point that runs both I/O adapters in one process:
- Queue consumer adapter (batch): polls Redis, calls `core.transcribe()`
- WebSocket adapter (realtime): accepts connections, calls
  `core.transcribe_stream()`

**Admission/QoS policy** (addresses P0 finding #3):

```python
class AdmissionController:
    """Prevents realtime starvation under batch load."""

    def __init__(self, config: AdmissionConfig):
        self.rt_reservation: int = config.rt_reservation  # min RT slots
        self.batch_max_inflight: int = config.batch_max_inflight
        self._active_rt: int = 0
        self._active_batch: int = 0

    def can_accept_batch(self) -> bool:
        """Reject batch if at inflight cap or RT needs reserved slots."""
        if self._active_batch >= self.batch_max_inflight:
            return False
        # Don't consume capacity reserved for RT
        total = self._active_batch + self._active_rt
        available = self.total_capacity - total
        return available > self.rt_reservation or self._active_rt >= self.rt_reservation

    def can_accept_rt(self) -> bool:
        """RT always gets its reserved slots; shares remaining with batch."""
        total = self._active_batch + self._active_rt
        return total < self.total_capacity
```

Configuration via environment variables:

```bash
DALSTON_RT_RESERVATION=2        # min slots reserved for realtime
DALSTON_BATCH_MAX_INFLIGHT=4    # max concurrent batch tasks
```

Both adapters check `AdmissionController` before accepting work.
Batch adapter returns task to queue (NACK) when rejected.
RT adapter returns 503 when rejected.

**Verify:** `make test` passes. Both batch and RT work from single process.
Add unit tests for `AdmissionController` edge cases.

### Phase 1.2: Roll to Other Runtimes

#### Step 1.2.1 — NeMo runtime shared core

Same pattern: extract `NemoTranscribeCore`, have batch+RT NeMo engines
delegate to it. One commit for core extraction, one for each engine
migration.

**Verify:** `make test` after each commit.

#### Step 1.2.2 — Remaining runtimes (nemo-onnx, hf-asr, vllm-asr)

One commit per runtime. Same pattern.

**Verify:** `make test` after each commit.

---

## PR-2: Unified Registry Model (Compat Mode)

*Single registry schema. Dual-read/dual-write during transition.*

### Phase 2.0: Safety Net

#### Step 2.0.1 — Contract tests for BatchEngineRegistry

Test current registration, heartbeat, deregistration, and query patterns.
File: `dalston/engine_sdk/registry.py` — class `BatchEngineRegistry`.

#### Step 2.0.2 — Contract tests for WorkerRegistry

Test current registration, heartbeat, worker state parsing, capacity
queries. File: `dalston/realtime_sdk/registry.py`.

**Verify:** `make test` passes.

### Phase 2.1: Unified Registry

#### Step 2.1.1 — Define `EngineRegistry` schema

New file `dalston/common/registry.py`:

```python
class EngineRecord(BaseModel):
    """Unified engine registration record."""
    instance: str
    runtime: str
    status: str  # ready, busy, draining, offline
    interfaces: list[str]  # ["batch"], ["realtime"], ["batch", "realtime"]
    capacity: int
    active_batch: int = 0
    active_realtime: int = 0
    models_loaded: list[str] = []
    languages: list[str] = []
    supports_word_timestamps: bool = False
    includes_diarization: bool = False
    gpu_memory_used: int = 0
    gpu_memory_total: int = 0
    last_heartbeat: datetime

    @property
    def available_capacity(self) -> int:
        return max(0, self.capacity - self.active_batch - self.active_realtime)

class EngineRegistry:
    """Unified async registry for all engine types."""

    async def register(self, record: EngineRecord) -> None: ...
    async def heartbeat(self, instance: str) -> None: ...
    async def deregister(self, instance: str) -> None: ...
    async def get_available(self, **filters) -> list[EngineRecord]: ...
    async def get_by_instance(self, instance: str) -> EngineRecord | None: ...
```

Nothing uses it yet.

**Verify:** `make test` passes.

#### Step 2.1.2 — Unified engine writes to both registries

Update the shared engine runner (from PR-1) to write to BOTH the new
unified registry AND the legacy registry keys. This is the dual-write
phase.

**Verify:** `make test` passes. Both old and new registry keys populated.

#### Step 2.1.3 — Orchestrator reads from unified registry

Switch orchestrator's engine discovery to query `EngineRegistry`.
Keep legacy reads as fallback behind a feature flag.

**Verify:** `make test` passes.

#### Step 2.1.4 — Remove legacy registry writes

Once all consumers read from unified registry, stop writing legacy keys.
Remove `BatchEngineRegistry` from `dalston/engine_sdk/registry.py`.

**Verify:** `make test && make lint` passes.

---

## PR-3: Gateway Realtime Proxy Core

*Extract shared allocation/forwarding/session logic from 3 WS handlers.*

### Phase 3.0: Safety Net

#### Step 3.0.1 — Contract tests per WebSocket protocol

Add `tests/unit/test_ws_protocol_contracts.py`:
- Test Dalston native protocol message shapes
- Test OpenAI compat protocol message translation
- Test ElevenLabs compat protocol message translation
- Assert each produces identical forwarded audio and returns valid
  transcript responses

**Verify:** `make test` passes.

### Phase 3.1: Extract Core

#### Step 3.1.1 — Create `RealtimeProxy` core module

Extract from the three WS handlers into
`dalston/gateway/services/realtime_proxy.py`:

```python
class RealtimeProxy:
    """Core realtime session lifecycle, shared by all WS adapters."""

    async def allocate_worker(self, language, model, runtime) -> WorkerAllocation: ...
    async def forward_audio(self, allocation, audio_bytes) -> None: ...
    async def receive_transcript(self, allocation) -> TranscriptChunk: ...
    async def release(self, allocation) -> None: ...
    async def keep_alive(self, session_id, interval=60) -> None: ...
```

This extracts the common logic from `_realtime_common.py` and the shared
patterns across all three handlers. ~300 LOC.

**Verify:** `make test` passes.

#### Step 3.1.2 — Dalston native WS uses `RealtimeProxy`

Refactor `realtime.py` to thin adapter (~200 LOC, down from 1,412).
Protocol translation only — no allocation or forwarding logic.

**Verify:** `make test` passes. Native WS protocol works end-to-end.

#### Step 3.1.3 — OpenAI compat WS uses `RealtimeProxy`

Refactor `openai_realtime.py` to thin adapter (~200 LOC, down from 1,354).

**Verify:** `make test` passes.

#### Step 3.1.4 — ElevenLabs compat adapter uses `RealtimeProxy`

Refactor `speech_to_text.py` WS path to thin adapter (~200 LOC, down
from 1,094).

**Verify:** `make test` passes.

---

## PR-4: Session Router Consolidation

*Migrate all session-router behaviors into orchestrator-owned coordination
service. This is NOT just moving acquire/release — it includes TTL
extension, orphan reconciliation, and offline instance fanout.*

### Behaviors to Migrate (addresses P0 finding #1)

The session router owns 6 distinct behaviors that ALL must be replicated:

| Behavior | Current Location | LOC |
|----------|-----------------|-----|
| Atomic capacity reservation + rollback | `allocator.py:115-225` | ~110 |
| Worker release + state cleanup | `allocator.py:299-351` | ~52 |
| Session TTL extension (keepalive) | `allocator.py:378-388` + gateway `_realtime_common.py:90-117` | ~40 |
| Orphaned session reconciliation | `health.py:149-237` | ~88 |
| Offline instance detection + event fanout | `health.py:100-147` | ~47 |
| Worker state queries + capacity filtering | `registry.py:61-325` | ~264 |

**Redis key schema to preserve** (or migrate):

| Key | Type | Purpose |
|-----|------|---------|
| `dalston:realtime:instances` | SET | All registered instance IDs |
| `dalston:realtime:instance:{id}` | HASH | Instance state (capacity, active_sessions, etc.) |
| `dalston:realtime:instance:{id}:sessions` | SET | Sessions on this instance |
| `dalston:realtime:session:{id}` | HASH | Session state + 300s TTL |
| `dalston:realtime:sessions:active` | SET | Global active session index |
| `dalston:realtime:events` | CHANNEL | Pub/Sub for offline notifications |

### Phase 4.0: Safety Net

#### Step 4.0.1 — Integration tests for session lifecycle

Add `tests/integration/test_session_lifecycle.py`:

- Test full cycle: allocate → keepalive → release
- Test capacity reservation: allocate until full, verify rejection
- Test race condition: concurrent allocations with rollback
- Test TTL extension: verify session survives beyond initial TTL
- Test orphan cleanup: simulate gateway crash (don't release), verify
  cleanup after TTL + monitor interval
- Test offline fanout: simulate worker heartbeat timeout, verify pub/sub
  event published with correct session IDs

These tests run against a real Redis instance.

**Verify:** `make test` passes.

### Phase 4.1: Migrate Behaviors

#### Step 4.1.1 — Add `SessionCoordinator` to orchestrator

New file `dalston/orchestrator/session_coordinator.py`:

```python
class SessionCoordinator:
    """Manages realtime session allocation, lifecycle, and recovery.

    Migrated from session_router with full behavioral parity:
    - Atomic capacity reservation with rollback on race
    - TTL-based session lifecycle with keepalive extension
    - Orphaned session reconciliation (gateway crash recovery)
    - Offline instance detection and Pub/Sub fanout
    """

    async def acquire(self, language, model, runtime) -> WorkerAllocation:
        """Atomic capacity reservation with rollback on race."""
        ...

    async def release(self, session_id: str) -> None:
        """Release session, decrement counters, clean indexes."""
        ...

    async def extend_ttl(self, session_id: str, ttl: int = 300) -> None:
        """Refresh session TTL (called by gateway keepalive)."""
        ...

    async def start_health_monitor(self) -> None:
        """Background loop: check_workers + reconcile_orphans every 10s."""
        ...

    async def _check_workers(self) -> None:
        """Detect stale heartbeats, mark offline, publish events."""
        ...

    async def _reconcile_orphaned_sessions(self) -> None:
        """Find expired session keys, clean up leaked state."""
        ...
```

This replicates ALL session router behaviors, not just acquire/release.
Uses the unified `EngineRegistry` from PR-2 for worker state queries.

**Verify:** `make test` passes. Integration tests from 4.0.1 pass against
`SessionCoordinator`.

#### Step 4.1.2 — Gateway calls `SessionCoordinator`

Update `RealtimeProxy` (from PR-3) to call `SessionCoordinator` instead
of `SessionRouter`. The proxy doesn't need to change its interface — only
the backing implementation changes.

**Verify:** `make test` passes. RT sessions work end-to-end.

#### Step 4.1.3 — Run both coordinators in parallel (validation)

For one release cycle, run BOTH session router and session coordinator.
Gateway uses coordinator, but session router's health monitor also runs.
Compare metrics between old and new to verify behavioral parity.

**Verify:** No orphaned sessions, no capacity leaks, no missed offline
events in monitoring.

#### Step 4.1.4 — Remove session router

Delete `dalston/session_router/` directory (~1,323 LOC).
Remove Docker service definition.
Remove `dalston/realtime_sdk/registry.py` (now superseded by unified
registry).

**Verify:** `make test && make lint` passes.

---

## PR-5: PII Post-Processing Migration

*Move PII detection + audio redaction to async post-processing. Ship behind
feature flag with blocking mode retained for compatibility.*

### Phase 5.0: Design

PII detection and audio redaction are compliance enrichments, not
transcript quality stages. Both can run after the core pipeline completes:

- **PII text redaction** needs transcript text + entity positions
- **Audio redaction** needs original audio (persists in S3 as prepare
  artifact) + PII entity timestamps

Neither modifies upstream pipeline outputs.

### Phase 5.1: Implementation

#### Step 5.1.1 — Add post-processing job framework

New file `dalston/orchestrator/post_processor.py`:

```python
class PostProcessor:
    """Runs async enrichment jobs after core pipeline completion."""

    async def schedule(self, job_id: UUID, enrichments: list[str]) -> None:
        """Schedule post-processing for completed job.

        Enrichments: ["pii_detect", "audio_redact"]
        """
        ...

    async def on_enrichment_complete(self, job_id: UUID, enrichment: str) -> None:
        """Handle enrichment completion. Update transcript."""
        ...
```

Post-processing reads transcript.json + original audio from S3, runs
PII engines, writes enriched transcript back.

**Verify:** `make test` passes.

#### Step 5.1.2 — Feature flag for async PII mode

```python
# dalston/common/settings.py
class PipelineSettings(BaseSettings):
    pii_mode: Literal["pipeline", "post_process"] = "pipeline"
```

- `pii_mode=pipeline` (default): current behavior, PII stages in DAG
- `pii_mode=post_process`: PII runs after core pipeline completes

**Verify:** `make test` passes with both modes.

#### Step 5.1.3 — DAG builder respects PII mode

When `pii_mode=post_process`:
- Omit `pii_detect` and `audio_redact` tasks from DAG
- After job completion, schedule post-processing via `PostProcessor`

When `pii_mode=pipeline` (default):
- Current behavior unchanged

**Verify:** `make test` passes. Both modes produce identical transcript.

#### Step 5.1.4 — Parity validation tests

Add test that runs the same job in both modes and asserts identical
transcript output (modulo timing metadata).

**Verify:** `make test` passes.

---

# Cycle 2: Pipeline Simplification (Deferred)

*These changes are in scope but deferred until Cycle 1 is stable in
production. Each is a separate PR.*

## PR-6: Shared Transcript Model

Define `Transcript` model in `pipeline_types.py`. Prove equivalence with
`MergeOutput`. Add `from_stage_outputs()` factory. This is additive —
nothing in production uses it yet.

## PR-7: Linear Pipeline (Eliminate Merge for Mono)

- Transcribe engines output `Transcript`
- Diarize engine enriches `Transcript` (adds speaker assignment via
  `find_speaker_by_overlap`, ~40 LOC moved from merge)
- Orchestrator writes last stage's output as `transcript.json`
- DAG builder omits merge for mono pipelines

**Transcript assembly determinism** (addresses P0 finding #2):
Do NOT use "last completed task" heuristic. Instead, the DAG builder
records the terminal stage name explicitly:

```python
@dataclass
class Pipeline:
    tasks: list[Task]
    terminal_stage: str  # e.g., "diarize" or "transcribe"
```

On job completion, the orchestrator reads the output of the task whose
stage matches `terminal_stage`. This is deterministic regardless of
retry ordering or parallel completion timing.

## PR-8: Per-Channel as Pre-Processing Split

**Deferred** pending usage telemetry. Current per-channel DAG works and
is tested. Rearchitecting as parent-child jobs introduces:
- API behavior changes (parent_job_id, child_job_ids)
- Job status semantics changes
- DB schema additions

These require API versioning or compatibility coverage that is out of
scope for this cycle (addresses P1 finding #6).

## PR-9: Collapse Align into Transcribe

Remove align as a separate stage. All modern transcribers (Whisper,
Parakeet) produce word-level timestamps natively. The align engine
becomes dead code. Keep it available as an optional post-processing
enrichment for edge cases (legacy models with poor attention-based
timestamps).

## PR-10: Declarative Engine.yaml + New Stages

Engines declare inputs/outputs in `engine.yaml`. Pipeline builder
auto-wires based on declarations. Adding new stages (VAD, emotion,
non-verbal) requires only engine.yaml + engine.py.

**Deferred** because it depends on stable Cycle 1 and is the highest-
risk change to the orchestrator's core loop (addresses P1 finding #4).

## PR-11: Clean Up (task_dependencies, MergeOutput, etc.)

**task_dependencies junction table** (addresses P1 finding #5):
Only dropped AFTER:
1. Linear pipeline is stable in production for ≥1 release cycle
2. No in-flight jobs exist that use the dependency table
3. Migration includes a drain check: reject if any active jobs exist
4. Rollback strategy: re-create table from pipeline stage ordering if
   needed (linear pipeline makes this trivial)

This is the LAST step, not an early cleanup.

---

## Execution Order Summary

```
Cycle 1 (this iteration):
  PR-1: Shared faster-whisper core + QoS admission (10 steps)
  PR-2: Unified registry with compat mode (6 steps)
  PR-3: Gateway WebSocket proxy core (5 steps)
  PR-4: Session router consolidation (5 steps)
  PR-5: PII post-processing behind feature flag (4 steps)

Cycle 2 (deferred):
  PR-6:  Shared Transcript model
  PR-7:  Linear pipeline + merge elimination
  PR-8:  Per-channel rearchitecture (pending telemetry)
  PR-9:  Align collapse
  PR-10: Declarative engine.yaml
  PR-11: Schema cleanup (task_dependencies, MergeOutput)
```

## Dependencies (Cycle 1)

```
PR-1 (Shared Engine) ──→ PR-2 (Unified Registry) ──→ PR-4 (Session Router)
                                                  ↗
                          PR-3 (WS Proxy Core) ──┘

PR-5 (PII Post-Processing) is independent
```

- **PR-1 first**: unified engine instance is the foundation
- **PR-2 after PR-1**: registry needs to know about dual-interface engines
- **PR-3 independent of PR-1/2**: pure gateway refactor
- **PR-4 after PR-2 + PR-3**: needs unified registry + proxy core
- **PR-5 independent**: can ship in parallel with anything

## Risk Register

| Risk | Impact | Mitigation |
|------|--------|-----------|
| RT starvation under batch load | Latency SLA breach | `AdmissionController` with `rt_reservation` + `batch_max_inflight` (PR-1 Step 1.1.4) |
| Registry migration loses state | Engine invisible | Dual-write/dual-read with explicit cutover checkpoints (PR-2 Step 2.1.2) |
| Session coordinator misses recovery behavior | Capacity leaks, stuck sessions | Full behavioral parity tests including orphan cleanup and offline fanout (PR-4 Step 4.0.1) |
| PII post-processing timing differs from pipeline | Compliance gap | Feature flag + parity validation tests (PR-5 Steps 5.1.2-5.1.4) |
| WS proxy extraction breaks protocol compat | Client errors | Contract tests per protocol (PR-3 Step 3.0.1) |
| Export formats break (SRT/VTT/TXT) | API break | Existing `test_export.py` runs after every step |

## File Paths Reference (addresses P2 finding #7)

Correct paths for current SDK layout:

| Component | Path |
|-----------|------|
| Batch Engine base class | `dalston/engine_sdk/base.py` → class `Engine` |
| Batch Engine runner | `dalston/engine_sdk/runner.py` → class `EngineRunner` |
| Batch registry | `dalston/engine_sdk/registry.py` |
| Batch model manager | `dalston/engine_sdk/model_manager.py` |
| Batch materializer | `dalston/engine_sdk/materializer.py` |
| Batch contracts | `dalston/engine_sdk/contracts.py` |
| RT Engine base class | `dalston/realtime_sdk/base.py` → class `RealtimeEngine` |
| RT session handler | `dalston/realtime_sdk/session.py` |
| RT registry | `dalston/realtime_sdk/registry.py` |
| RT model manager | `dalston/realtime_sdk/model_manager.py` |
| RT VAD | `dalston/realtime_sdk/vad.py` |
| RT assembler | `dalston/realtime_sdk/assembler.py` |
| Session router | `dalston/session_router/router.py` |
| Session allocator | `dalston/session_router/allocator.py` |
| Session health monitor | `dalston/session_router/health.py` |
| Worker registry (RT) | `dalston/session_router/registry.py` |
| Faster-whisper batch | `engines/stt-transcribe/faster-whisper/engine.py` |
| Faster-whisper RT | `engines/stt-rt/faster-whisper/engine.py` |
| Merge engine | `engines/stt-merge/final-merger/engine.py` |

## Estimated Cycle 1 Total

- **PR-1:** ~10 steps
- **PR-2:** ~6 steps
- **PR-3:** ~5 steps
- **PR-4:** ~5 steps
- **PR-5:** ~4 steps
- **Total:** ~30 steps / commits
