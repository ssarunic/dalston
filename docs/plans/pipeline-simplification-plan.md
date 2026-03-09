# Architecture Simplification: Full Implementation Plan

## Scope

This plan covers ALL seven workstreams from the complexity review, not just
pipeline/merge simplification. They are sequenced so each builds on the
previous, and each step is the smallest possible change with tests.

## Guiding Principles

- **One logical change per step.** Each step is a single commit.
- **Tests first.** Add characterization tests before refactoring.
- **`make test` after every commit.** Red = stop and fix before proceeding.
- **`make lint` after every commit.** No regressions.
- **Backward-compatible API output.** Gateway consumers see identical JSON
  throughout the entire refactor.
- **No big bang.** Old and new code coexist during transition.

---

## Workstream 1: Linear Pipeline + Merge Elimination

*Highest-leverage change. Replace fork-join DAG with linear pipeline.
Eliminate the 1,141-line merge engine. ~1,800 LOC deleted, ~200 LOC added.*

### Phase 1.0: Harden the Safety Net

*No production code changes. Lock down current behavior.*

#### Step 1.0.1 — Unit tests for merge helper functions

Add `tests/unit/test_merge_helpers.py` testing (import `_`-prefixed
functions directly from the merge engine module):

- `_find_speaker_by_overlap()` — no turns, full overlap, partial overlap,
  multi-speaker, tie-breaking
- `_normalize_words()` — dict→Word, empty list, missing fields
- `_apply_known_speaker_names()` — relabel by first appearance, more/fewer
  names than speakers
- `_redact_mono_segment_text()` — PII time-overlap matching
- `_redact_segment_text()` — per-channel PII matching

**Verify:** `make test` passes.

#### Step 1.0.2 — Contract tests for merge engine output

Add `tests/unit/test_merge_output_contract.py`:

- Build `EngineInput` fixtures for each variant:
  1. Mono, no diarization, no PII
  2. Mono + diarization, no PII
  3. Mono + diarization + PII
  4. Per-channel (2ch), no PII
  5. Per-channel (2ch) + PII + audio redaction
- Call `FinalMergerEngine.process()`, assert output JSON structure:
  `job_id`, `version`, `metadata`, `text`, `segments[]`, `speakers[]`
- Snapshot output for regression comparison

**Verify:** `make test` passes.

#### Step 1.0.3 — DAG structure snapshot tests

Add `tests/unit/test_dag_snapshots.py`:

- For each variant (mono, mono+diarize, mono+diarize+PII, per-channel,
  per-channel+PII), snapshot: stage names, dependency edges, task count
- More explicit than existing `test_dag.py` about full graph shape

**Verify:** `make test` passes.

---

### Phase 1.1: Extract Merge Helpers

*Pure refactor. Move functions out of merge engine into reusable modules.
No behavior change.*

#### Step 1.1.1 — Create `dalston/common/transcript_ops.py`

Extract from `engines/stt-merge/final-merger/engine.py`:

```python
find_speaker_by_overlap(seg_start, seg_end, turns) -> str | None
normalize_words(words: list[dict]) -> list[Word]
apply_known_speaker_names(segments, speakers, names) -> tuple[list, list]
```

Update merge engine to import these. Update test imports.

**Verify:** `make test` passes.

#### Step 1.1.2 — Create `dalston/common/pii_ops.py`

Extract PII text redaction functions:

```python
redact_segment_text(segment, pii_entities, ...) -> str | None
redact_mono_segment_text(segment, pii_entities, ...) -> str | None
```

**Verify:** `make test` passes.

#### Step 1.1.3 — Create `dalston/common/audio_ops.py`

Extract FFmpeg stereo assembly:

```python
assemble_stereo_audio(channel_paths: list[Path], output_path: Path) -> Path
```

**Verify:** `make test` passes.

#### Step 1.1.4 — Verify merge engine is now a thin orchestrator

Merge engine `process()` should read as a short sequence of calls to the
extracted functions. No new tests — existing tests cover this. Verify
line count dropped significantly.

**Verify:** `make test` passes.

---

### Phase 1.2: Shared Transcript Model

*Additive change. New model alongside existing ones. Nothing uses it yet.*

#### Step 1.2.1 — Add `Transcript` model to `pipeline_types.py`

```python
class Transcript(BaseModel):
    """Progressively enriched transcript. The single document that
    flows through the pipeline, getting richer at each stage."""

    job_id: str
    version: str = "1.0"
    text: str = ""
    segments: list[MergedSegment] = []
    speakers: list[Speaker] = []
    language: str | None = None
    language_confidence: float | None = None
    metadata: TranscriptMetadata | None = None
    redacted_text: str | None = None
    pii_entities: list[PIIEntity] = []
    pii_metadata: PIIMetadata | None = None
    enrichments: list[str] = []
```

Add unit tests. Add equivalence test: `Transcript.model_dump()` matches
`MergeOutput.model_dump()` when fully populated.

**Verify:** `make test` passes.

#### Step 1.2.2 — Add `Transcript.from_merge_output()` bridge

Class method to convert existing `MergeOutput` → `Transcript`.
Test round-trip: `MergeOutput → Transcript → dict` == `MergeOutput → dict`.

**Verify:** `make test` passes.

#### Step 1.2.3 — Add `Transcript.from_stage_outputs()` factory

Builds a `Transcript` directly from stage outputs (same logic as merge
engine: choose segment source, apply speaker overlap, apply PII). This is
a parallel implementation — NOT yet used in production.

Test: call with same fixtures as Step 1.0.2, assert output matches merge
engine output.

**Verify:** `make test` passes. Equivalence tests pass.

---

### Phase 1.3: Migrate Merge Engine to Transcript

*Merge engine delegates to Transcript model. API output unchanged.*

#### Step 1.3.1 — Mono path uses `Transcript.from_stage_outputs()`

Replace inline logic in `process()` with call to factory.

**Verify:** `make test` passes. Contract tests produce identical output.

#### Step 1.3.2 — Per-channel path uses Transcript

Build per-channel Transcript objects, interleave, combine speakers,
handle stereo audio assembly.

**Verify:** `make test` passes.

#### Step 1.3.3 — Return `Transcript` instead of `MergeOutput`

Transparent to gateway (identical JSON proven by equivalence tests).

**Verify:** `make test` passes. Export tests pass.

---

### Phase 1.4: Linear Pipeline (Eliminate Merge for Mono)

*Architectural change to DAG structure.*

#### Step 1.4.1 — Diarize engine enriches Transcript

Update diarize engine to:
1. Run pyannote on audio → get speaker turns (as today)
2. Read `previous_outputs["transcribe"]` to get transcript segments
3. Apply `find_speaker_by_overlap()` to assign speakers to segments
4. Apply `apply_known_speaker_names()` if configured
5. Output enriched `Transcript` (with `segments[].speaker` populated)

The diarize engine gains ~40 LOC of speaker assignment logic (moved from
merge). It still outputs `DiarizeOutput` in parallel for backward compat,
but ALSO writes the enriched `Transcript`.

**Verify:** `make test` passes. Diarize + speaker assignment tested.

#### Step 1.4.2 — Transcribe engine outputs Transcript

Update transcribe engines to output a `Transcript` object (segments,
text, language) in addition to their current `TranscribeOutput`.

The `Transcript` written by transcribe is the initial document that
flows through the pipeline.

**Verify:** `make test` passes.

#### Step 1.4.3 — Orchestrator assembles transcript when no merge task

In `handlers.py`, when the last task completes and there's no merge task
in the DAG:

```python
async def _assemble_transcript(job_id, tasks, settings):
    # Read the last stage's output — it IS the Transcript
    last_task = find_last_completed_task(tasks)
    transcript = await get_task_output(job_id, last_task.id, settings)
    await store_transcript(job_id, transcript, settings)
```

Integration test: mono DAG (no merge) produces identical transcript.

**Verify:** `make test` passes.

#### Step 1.4.4 — DAG builder: omit merge for mono pipelines

Update `build_task_dag` in `dag.py`:
- When `speaker_detection != per_channel`: omit merge task
- Pipeline becomes: `prepare → transcribe → [diarize]`
- Each stage writes `Transcript` forward; last stage's output is final

Update DAG tests for new pipeline shapes.

**Verify:** `make test` passes.

#### Step 1.4.5 — Remove mono logic from merge engine

Merge engine now only handles per-channel. Remove mono path.
Optionally rename to `ChannelMergerEngine`.

**Verify:** `make test` passes.

---

### Phase 1.5: Per-Channel as Pre-Processing Split

*Replace per-channel DAG variant with parent-child jobs. ~1,200 LOC deleted.*

#### Step 1.5.1 — Add `split_channels()` utility

Add to `dalston/common/audio_ops.py`:

```python
def split_channels(input_path: Path, output_dir: Path) -> list[Path]:
    """Split stereo WAV into N mono WAVs using FFmpeg."""
```

~20 LOC. Unit test with a test WAV file.

**Verify:** `make test` passes.

#### Step 1.5.2 — Add parent-child job relationship

In gateway/orchestrator, when `speaker_detection=per_channel`:
1. Gateway splits stereo file into N mono files
2. Submits N child jobs (each running normal mono pipeline)
3. Parent job tracks children
4. Gateway presents parent job as single status endpoint

Add DB fields: `parent_job_id`, `child_job_ids` on job model.

**Verify:** `make test` passes.

#### Step 1.5.3 — Add `stitch_per_channel_results()` post-processor

When all child jobs complete:
1. Read each child's `Transcript`
2. Interleave segments by timestamp
3. Label speakers by channel index
4. Optionally reassemble stereo redacted audio

~80 LOC. Test against current per-channel merge output.

**Verify:** `make test` passes.

#### Step 1.5.4 — Remove per-channel DAG code

Delete:
- `_build_per_channel_dag_with_engines()` (~210 LOC)
- Per-channel branches in dag builder (~15 LOC)
- Per-channel merge logic in merge engine (~670 LOC)
- `_ch{N}` suffix parsing in handlers.py (~30 LOC)
- Per-channel PII logic in merger (~100 LOC)

Update/remove per-channel tests.

**Verify:** `make test` passes.

#### Step 1.5.5 — Delete merge engine entirely

With mono handled by linear pipeline and per-channel handled by
parent-child jobs + stitcher, the merge engine has no remaining purpose.

Delete `engines/stt-merge/final-merger/` directory and Docker service.

**Verify:** `make test` passes.

---

### Phase 1.6: Clean Up

#### Step 1.6.1 — Remove `MergeOutput`, `MergeInput` from pipeline_types

Replace all references with `Transcript`.

**Verify:** `make test && make lint` passes.

#### Step 1.6.2 — Simplify `_gather_previous_outputs`

With a linear pipeline, this becomes "read the previous stage's output"
instead of querying a dependency graph. Simplify accordingly.

**Verify:** `make test` passes.

#### Step 1.6.3 — Drop `task_dependencies` junction table

DB migration to remove the now-unused table. Remove
`TaskModel.dependency_links` relationship.

**Verify:** `make test` passes.

---

## Workstream 2: Unify Engine SDK

*Single base class with optional streaming. ~3,000-4,000 LOC duplication
eliminated.*

### Phase 2.0: Characterization Tests

#### Step 2.0.1 — Contract tests for batch Engine base class

Test the current `Engine` base class contract: `__init__`, `process()`,
health check, model loading, heartbeat registration.

#### Step 2.0.2 — Contract tests for RealtimeEngine base class

Test the current `RealtimeEngine` contract: `__init__`, `process_chunk()`,
session lifecycle, WebSocket handling.

**Verify:** `make test` passes.

---

### Phase 2.1: Unified Engine Interface

#### Step 2.1.1 — Define unified `Engine` ABC

```python
class Engine(ABC):
    @abstractmethod
    def load_model(self, model_id: str) -> None: ...

    @abstractmethod
    def process_file(self, audio_path: Path, config: dict) -> Transcript: ...

    def process_chunk(self, audio: np.ndarray, config: dict) -> ChunkResult:
        raise NotImplementedError("Streaming not supported")

    def supports_streaming(self) -> bool:
        return False
```

New file `dalston/engine_sdk/unified.py`. Nothing uses it yet.

**Verify:** `make test` passes.

#### Step 2.1.2 — Unified `EngineRunner` with dual I/O

Runner that checks `supports_streaming()`:
- Always: start Redis queue consumer (batch)
- If streaming: also start WebSocket server (realtime)

New file `dalston/engine_sdk/unified_runner.py`.

**Verify:** `make test` passes.

#### Step 2.1.3 — Migrate faster-whisper batch engine to unified ABC

Update `engines/stt-transcribe/faster-whisper/engine.py` to extend unified
`Engine`. Keep old import paths working via re-export.

**Verify:** `make test` passes. Batch transcription produces identical output.

#### Step 2.1.4 — Migrate faster-whisper RT engine to unified ABC

Merge `engines/stt-rt/faster-whisper/engine.py` into the batch engine by
implementing `process_chunk()` and `supports_streaming() → True`.

This eliminates ~335 LOC of the duplicate RT implementation.

**Verify:** `make test` passes. RT transcription works.

#### Step 2.1.5 — Migrate remaining batch engines

One commit per engine (prepare, diarize, pii-detect, audio-redact).
These are batch-only so they only implement `process_file()`.

**Verify:** `make test` after each.

#### Step 2.1.6 — Migrate remaining RT engines

One commit per RT runtime (nemo, nemo-onnx, hf-asr, vllm-asr).
Merge into corresponding batch engine where one exists.

**Verify:** `make test` after each.

#### Step 2.1.7 — Remove old SDK base classes

Delete `dalston/engine_sdk/engine.py` (old batch Engine) and
`dalston/realtime_sdk/engine.py` (old RealtimeEngine) once all engines
are migrated.

**Verify:** `make test && make lint` passes.

---

## Workstream 3: Declarative Engine.yaml

*Engines declare their inputs/outputs. Pipeline auto-wires.*

### Phase 3.1: Schema Extension

#### Step 3.1.1 — Extend engine.yaml with input/output declarations

Add optional fields to existing engine.yaml schema:

```yaml
stage:
  name: transcribe
  inputs:
    - kind: audio
      role: prepared
  outputs:
    - kind: transcript
      role: enriched
  subsumes: [align]  # native word timestamps
```

Existing engines continue working — new fields are optional.

**Verify:** `make test` passes.

#### Step 3.1.2 — Add engine.yaml declarations to each engine

Update each engine's `engine.yaml` with input/output declarations.
One commit per engine or batch if trivial.

**Verify:** `make test` passes.

---

### Phase 3.2: Generic Pipeline Builder

#### Step 3.2.1 — New pipeline builder using engine declarations

Replace the hardcoded `build_task_dag` with a generic builder:

```python
def build_pipeline(job_params, available_engines):
    requested_outputs = derive_requested_outputs(job_params)
    stages = resolve_stage_sequence(requested_outputs, available_engines)
    return [make_task(stage) for stage in stages]
```

Run in parallel with old builder, assert identical output.

**Verify:** `make test` passes.

#### Step 3.2.2 — Switch to new builder, delete old dag.py

**Verify:** `make test` passes.

---

## Workstream 4: Unify Registry

*Single async EngineRegistry replacing BatchEngineRegistry + WorkerRegistry.*

#### Step 4.1 — Define unified `EngineRegistry`

Single async class. Stores capabilities including `supports_streaming`.
New file, nothing uses it yet.

**Verify:** `make test` passes.

#### Step 4.2 — Engines register via unified registry

Update unified `EngineRunner` to use new registry.
Run old + new registries in parallel during transition.

**Verify:** `make test` passes.

#### Step 4.3 — Orchestrator uses unified registry

Switch orchestrator to query unified registry.

**Verify:** `make test` passes.

#### Step 4.4 — Session router uses unified registry

Switch session router to query unified registry for streaming-capable
engines.

**Verify:** `make test` passes.

#### Step 4.5 — Remove old registries

Delete `BatchEngineRegistry` and `WorkerRegistry`.

**Verify:** `make test && make lint` passes.

---

## Workstream 5: Collapse Session Router into Orchestrator

*Unified engine discovery and allocation. ~1,300 LOC eliminated.*

#### Step 5.1 — Add session allocation to orchestrator

Move `acquire_worker()` / `release_worker()` logic into orchestrator's
registry. ~100 LOC.

**Verify:** `make test` passes.

#### Step 5.2 — Gateway calls orchestrator for RT sessions

Update gateway's WebSocket handlers to request RT sessions from
orchestrator instead of session router.

**Verify:** `make test` passes. RT sessions work.

#### Step 5.3 — Delete session router

Remove `dalston/session_router/` (~1,300 LOC).
Remove Docker service.

**Verify:** `make test && make lint` passes.

---

## Workstream 6: Extract WebSocket Proxy Core

*Reduce 3,860 LOC of WS handlers to ~300 LOC core + ~200 LOC per adapter.*

#### Step 6.1 — Extract `RealtimeProxy` core

Common logic: worker allocation, audio forwarding, transcript collection,
session lifecycle. ~300 LOC.

**Verify:** `make test` passes.

#### Step 6.2 — Dalston native WS adapter

Refactor `realtime.py` to thin adapter over `RealtimeProxy`.
~200 LOC (down from 1,412).

**Verify:** `make test` passes. Native WS protocol works.

#### Step 6.3 — OpenAI compat WS adapter

Refactor `openai_realtime.py` to thin adapter.
~200 LOC (down from 1,354).

**Verify:** `make test` passes.

#### Step 6.4 — ElevenLabs compat adapter

Refactor `speech_to_text.py` to thin adapter.
~200 LOC (down from 1,094).

**Verify:** `make test` passes.

---

## Workstream 7: New Stages (Unlocked by Previous Work)

*After declarative engine.yaml, adding new stages is trivial.*

Each new stage requires ONLY:
1. Write `engine.py` + `engine.yaml` declaring inputs/outputs
2. Write Dockerfile
3. Done — no orchestrator/DAG/selector changes

Future stages (noise removal, VAD, emotion, non-verbal events, LLM
cleanup) follow this pattern. No plan detail needed — the framework
handles them automatically.

---

## Execution Order Summary

```
Workstream 1: Linear Pipeline + Merge Elimination
  Phase 1.0: Safety net tests (3 steps)
  Phase 1.1: Extract merge helpers (4 steps)
  Phase 1.2: Transcript model (3 steps)
  Phase 1.3: Migrate merge engine (3 steps)
  Phase 1.4: Linear pipeline (5 steps)
  Phase 1.5: Per-channel as pre-processing (5 steps)
  Phase 1.6: Clean up (3 steps)

Workstream 2: Unify Engine SDK
  Phase 2.0: Characterization tests (2 steps)
  Phase 2.1: Unified interface + migration (7 steps)

Workstream 3: Declarative Engine.yaml
  Phase 3.1: Schema extension (2 steps)
  Phase 3.2: Generic pipeline builder (2 steps)

Workstream 4: Unify Registry (5 steps)

Workstream 5: Collapse Session Router (3 steps)

Workstream 6: WebSocket Proxy Core (4 steps)

Workstream 7: New Stages (unlocked, no fixed steps)
```

## Dependencies Between Workstreams

```
WS1 (Linear Pipeline) ──────┐
                              ├──→ WS3 (Declarative engine.yaml) ──→ WS7 (New Stages)
WS2 (Unify Engine SDK) ──────┘
                              │
                              ├──→ WS4 (Unify Registry)
                              │
                              └──→ WS5 (Collapse Session Router)

WS6 (WebSocket Proxy) is independent — can run in parallel with anything
```

- **WS1 and WS2 can run in parallel** (different code areas)
- **WS3 depends on WS1 + WS2** (needs linear pipeline + unified engine)
- **WS4 depends on WS2** (needs unified SDK for single registration)
- **WS5 depends on WS4** (needs unified registry)
- **WS6 is independent** — pure gateway refactor, no pipeline/engine deps
- **WS7 depends on WS3** (needs declarative engine.yaml)

## Risk Register

| Risk | Impact | Mitigation |
|------|--------|-----------|
| Transcript ≠ MergeOutput JSON | API break | Equivalence test (Step 1.2.2) catches before migration |
| Per-channel parent-child jobs add latency | UX | Benchmark; FFmpeg split is ~10ms, job overhead is the concern |
| Unified engine breaks RT latency | UX | Benchmark `process_chunk()` before/after |
| Declarative pipeline builder misses edge case | Wrong DAG | Run old + new builders in parallel (Step 3.2.1) |
| Session router collapse breaks RT allocation | Outage | Feature flag, gradual rollout |
| Export formats break (SRT/VTT/TXT) | API break | Existing test_export.py runs after every step |

## Estimated Total

- **Workstream 1:** ~26 steps (~26 commits)
- **Workstream 2:** ~9 steps
- **Workstream 3:** ~4 steps
- **Workstream 4:** ~5 steps
- **Workstream 5:** ~3 steps
- **Workstream 6:** ~4 steps
- **Total:** ~51 steps / commits

## LOC Impact Estimate

| Workstream | Deleted | Added | Net |
|-----------|---------|-------|-----|
| WS1: Linear pipeline | ~3,500 | ~500 | -3,000 |
| WS2: Unify SDK | ~4,000 | ~1,500 | -2,500 |
| WS3: Declarative yaml | ~700 | ~300 | -400 |
| WS4: Unify registry | ~800 | ~300 | -500 |
| WS5: Session router | ~1,300 | ~100 | -1,200 |
| WS6: WS proxy | ~3,000 | ~900 | -2,100 |
| **Total** | **~13,300** | **~3,600** | **~-9,700** |
