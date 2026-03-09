# Pipeline Simplification: Implementation Plan

## Goal

Refactor the batch pipeline to use a shared `Transcript` model that stages
progressively enrich, reducing the 1,141-line merge engine to a thin assembler
(or eliminating it for mono pipelines).

## Guiding Principles

- **One logical change per step.** Each step is a single commit.
- **Tests before and after.** Add characterization tests first, then refactor.
- **No behavior change until Phase 4.** Phases 1-3 are pure refactors.
- **Run `make test` after every commit.** Red = stop and fix before proceeding.
- **Backward-compatible API output.** Gateway consumers see identical JSON.

---

## Phase 1: Harden the Safety Net

*Add characterization tests that lock down current behavior so we can refactor
with confidence. No production code changes.*

### Step 1.1 — Unit tests for merge helper functions

Add `tests/unit/test_merge_helpers.py` covering:

- `_find_speaker_by_overlap(seg_start, seg_end, turns)` — exact overlap
  calculation with edge cases (no turns, full overlap, partial overlap,
  multiple speakers, ties)
- `_normalize_words(words)` — dict-to-Word conversion, empty list, missing
  fields
- `_apply_known_speaker_names(segments, speakers, names)` — relabeling by
  first appearance, more names than speakers, fewer names than speakers
- `_redact_mono_segment_text(...)` — PII entity time-overlap matching
- `_redact_segment_text(...)` — per-channel PII matching

These functions are currently private in `engine.py`. To test them without
exposing internals, import them directly from the module (Python allows
importing `_`-prefixed names). This is acceptable for characterization tests
that will be replaced later.

**Test:** `make test` passes. New tests pass.

### Step 1.2 — Contract test for merge engine output shape

Add `tests/unit/test_merge_output_contract.py`:

- Construct representative `EngineInput` fixtures for each pipeline variant:
  1. Mono, no diarization, no PII
  2. Mono, with diarization, no PII
  3. Mono, with diarization, with PII
  4. Per-channel (2ch), no PII
  5. Per-channel (2ch), with PII + audio redaction
- Call `FinalMergerEngine.process()` with each fixture
- Assert output JSON structure matches expected schema:
  - `job_id`, `version`, `metadata`, `text`, `segments`, `speakers`
  - Each segment has `id`, `start`, `end`, `text`, `speaker` (when diarized)
  - Metadata has `audio_duration`, `audio_channels`, `language`,
    `pipeline_stages`
- Snapshot the output JSON for regression comparison

This test proves we haven't broken the API contract at any point during
the refactor.

**Test:** `make test` passes. Contract tests pass.

### Step 1.3 — DAG structure snapshot tests

Add `tests/unit/test_dag_snapshots.py`:

- For each pipeline variant (mono, mono+diarize, per-channel,
  per-channel+PII), call `build_task_dag_for_test` and snapshot:
  - Stage names in topological order
  - Dependency edges
  - Task count
- These overlap with existing `test_dag.py` but are more explicit about the
  full graph shape.

**Test:** `make test` passes.

---

## Phase 2: Extract Merge Helpers into a Shared Module

*Pure refactor. Move functions out of the 1,141-line merge engine into
reusable modules. Merge engine delegates to them. No behavior change.*

### Step 2.1 — Create `dalston/common/transcript_ops.py`

Extract these functions from `engines/stt-merge/final-merger/engine.py`:

```
find_speaker_by_overlap(seg_start, seg_end, turns) -> str | None
normalize_words(words: list[dict]) -> list[Word]
apply_known_speaker_names(segments, speakers, names) -> dict
```

These are pure functions with no engine dependencies. Move them, update the
merge engine to import from the new module, update the test imports.

**Test:** `make test` passes. All merge tests still pass. Helper unit tests
still pass with updated imports.

### Step 2.2 — Extract PII text redaction into `dalston/common/pii_ops.py`

Extract:

```
redact_segment_text(segment, pii_entities, ...) -> str | None
redact_mono_segment_text(segment, pii_entities, ...) -> str | None
```

Same pattern: pure functions, no engine deps.

**Test:** `make test` passes.

### Step 2.3 — Extract per-channel stereo assembly

Extract into `dalston/common/audio_ops.py`:

```
assemble_stereo_audio(channel_paths: list[Path], output_path: Path) -> Path
```

This wraps the FFmpeg subprocess call. Self-contained.

**Test:** `make test` passes.

### Step 2.4 — Verify merge engine is now a thin orchestrator

At this point, `engine.py` should be significantly shorter. The `process()`
method should read as:

1. Gather inputs
2. Determine segment source (align > transcribe > raw)
3. Build segments with `find_speaker_by_overlap`
4. Apply PII redaction with `redact_segment_text`
5. Apply speaker names with `apply_known_speaker_names`
6. Assemble metadata
7. Write artifact

No new tests needed — existing tests cover this. Just verify line count
dropped and readability improved.

**Test:** `make test` passes.

---

## Phase 3: Define the Transcript Model

*Additive change. New model alongside existing ones. Nothing uses it yet.*

### Step 3.1 — Add `Transcript` model to `dalston/common/pipeline_types.py`

Define a new model that represents a transcript at any stage of enrichment:

```python
class Transcript(BaseModel):
    """Progressively enriched transcript.

    Starts with segments from transcription, gains word timestamps
    from alignment, speaker labels from diarization, PII annotations
    from detection, and audio redaction references from redaction.
    """
    job_id: str
    version: str = "1.0"

    # Core content (from transcribe)
    text: str
    segments: list[MergedSegment]  # Reuse existing MergedSegment
    language: str | None = None
    language_confidence: float | None = None

    # Speaker info (from diarize)
    speakers: list[Speaker] = []

    # Audio metadata (from prepare)
    metadata: TranscriptMetadata | None = None

    # PII (from pii_detect + audio_redact)
    redacted_text: str | None = None
    pii_entities: list[PIIEntity] = []
    pii_metadata: PIIMetadata | None = None

    # Enrichment tracking
    enrichments: list[str] = []  # ["transcribed", "aligned", "diarized", ...]
```

Add tests in `test_pipeline_types.py` for the new model.

Ensure `Transcript.model_dump()` produces JSON that is **structurally
identical** to `MergeOutput.model_dump()` when fully populated. Add a test
that proves this equivalence.

**Test:** `make test` passes. New model tests pass.

### Step 3.2 — Add `Transcript.from_merge_output()` factory method

Add a class method that converts a `MergeOutput` into a `Transcript`. This
provides the migration bridge: existing code produces `MergeOutput`, new code
can convert it.

Add test proving round-trip: `MergeOutput → Transcript → dict` produces
identical JSON to `MergeOutput → dict`.

**Test:** `make test` passes.

### Step 3.3 — Add `Transcript.from_stage_outputs()` factory method

Add a class method that builds a `Transcript` directly from stage outputs:

```python
@classmethod
def from_stage_outputs(
    cls,
    job_id: str,
    prepare: PrepareOutput | None,
    transcribe: TranscribeOutput | None,
    align: AlignOutput | None,
    diarize: DiarizeOutput | None,
    pii_detect: PIIDetectOutput | None,
    audio_redact: AudioRedactOutput | None,
    config: dict | None = None,
) -> Transcript:
```

This method contains the **same logic** currently in the merge engine:
- Choose segment source (align > transcribe)
- Apply speaker overlap matching
- Apply PII redaction
- Build metadata

Critically: this does NOT replace the merge engine yet. It's a parallel
implementation we can test against merge output for equivalence.

Add test: call `from_stage_outputs()` with the same inputs as the merge
engine contract tests from Step 1.2, assert output matches.

**Test:** `make test` passes. Equivalence tests pass.

---

## Phase 4: Migrate Merge Engine to Use Transcript

*First behavior-adjacent change. Merge engine delegates to Transcript model.*

### Step 4.1 — Merge engine uses `Transcript.from_stage_outputs()` for mono

Update `FinalMergerEngine.process()` for the **mono (non-per-channel) path
only**:

```python
# Before: 200+ lines of inline logic
# After:
transcript = Transcript.from_stage_outputs(
    job_id=..., prepare=..., transcribe=..., align=...,
    diarize=..., pii_detect=..., audio_redact=...,
    config=input.config,
)
```

Convert `Transcript` to `MergeOutput` for the return value (API contract
unchanged).

**Test:** `make test` passes. Contract tests from Step 1.2 still produce
identical output.

### Step 4.2 — Merge engine uses `Transcript` for per-channel path

Same approach for `_merge_per_channel()`. This is more complex because of
multi-channel segment interleaving and stereo audio assembly.

Update per-channel logic to:
1. Build per-channel `Transcript` objects
2. Merge them (interleave by time, combine speakers)
3. Handle stereo audio assembly (still uses `audio_ops.assemble_stereo_audio`)

**Test:** `make test` passes. Per-channel contract tests produce identical
output.

### Step 4.3 — Merge engine returns `Transcript` directly

Change `EngineOutput.data` from `MergeOutput` to `Transcript`.

Since `Transcript` and `MergeOutput` produce identical JSON (proven by Step
3.2 tests), this is transparent to the gateway.

Update the merge output contract tests to assert on `Transcript` type.

**Test:** `make test` passes. Export tests pass. Gateway integration tests
pass.

---

## Phase 5: Make Merge Optional for Mono Pipelines

*Architectural change. For simple mono pipelines, the orchestrator can skip
merge entirely and have the last stage write the transcript artifact.*

### Step 5.1 — Add transcript artifact writing to `Transcript` model

Add a method to `Transcript` that serializes to the canonical `transcript.json`
format expected by the gateway:

```python
def to_artifact_json(self) -> bytes:
    return self.model_dump_json(indent=2).encode()
```

**Test:** Output matches what merge engine currently writes.

### Step 5.2 — Orchestrator: build Transcript from stage outputs when merge is skipped

In `dalston/orchestrator/handlers.py`, add logic for when the DAG has no
merge task:

```python
async def _assemble_transcript(job_id, completed_tasks, settings):
    """Build and store transcript directly from stage outputs."""
    outputs = await _gather_all_outputs(job_id, completed_tasks, settings)
    transcript = Transcript.from_stage_outputs(job_id=str(job_id), **outputs)
    await store_transcript_artifact(job_id, transcript, settings)
```

This runs after the last task completes (when there's no merge task).

**Test:** Integration test with a mono DAG (no merge task) produces
identical transcript to one that goes through merge.

### Step 5.3 — DAG builder: skip merge for mono pipelines

Update `build_task_dag` in `dag.py`:

- When `speaker_detection != per_channel` AND `redact_pii_audio` is false:
  omit the merge task from the DAG
- The orchestrator's job-completion handler assembles the transcript instead

Update DAG tests to reflect new pipeline shapes. Existing snapshot tests
from Step 1.3 will need updating — this is expected since the DAG shape
changed.

**Test:** `make test` passes. Mono pipeline produces identical output.
Per-channel pipeline still uses merge engine.

### Step 5.4 — Simplify merge engine to per-channel + audio assembly only

Now that mono pipelines don't need merge:

- Remove mono path from `FinalMergerEngine.process()`
- Merge engine only handles per-channel segment interleaving and stereo
  audio assembly
- Rename to `ChannelMergerEngine` (optional, cosmetic)

**Test:** `make test` passes. Per-channel pipeline still works.

---

## Phase 6: Clean Up

### Step 6.1 — Remove `MergeOutput` model

If all consumers now use `Transcript`:
- Remove `MergeOutput` from `pipeline_types.py`
- Remove `from_merge_output()` bridge method from `Transcript`
- Update any remaining references

**Test:** `make test` passes. `make lint` passes.

### Step 6.2 — Update `MergeInput` for per-channel only

Rename `MergeInput` → `ChannelMergeInput` if merge was renamed. Remove
fields that only applied to mono merging (e.g., `merge_strategy`).

**Test:** `make test` passes.

### Step 6.3 — Update documentation

- Update `CLAUDE.md` pipeline stage list
- Update `docs/` architecture docs
- Update API docs if transcript schema changed

**Test:** Review only, no code tests needed.

---

## Risk Register

| Risk | Mitigation |
|------|-----------|
| Transcript ≠ MergeOutput JSON shape | Step 3.2 equivalence test catches this before any migration |
| Per-channel stereo assembly breaks | Step 1.2 contract test with audio fixture catches this |
| Gateway expects MergeOutput-specific fields | Step 1.2 contract test covers gateway's `get_transcript()` path |
| Export formats break (SRT/VTT/TXT) | Existing `test_export.py` catches this — run after every step |
| DAG change breaks orchestrator job completion | Step 5.2 integration test covers the new completion path |
| Engine SDK `get_merge_output()` helper breaks | Add deprecation, keep working via `get_raw_output("merge")` fallback |

## Files Modified (by phase)

| Phase | Files | Type |
|-------|-------|------|
| 1 | `tests/unit/test_merge_helpers.py` (new) | Test |
| 1 | `tests/unit/test_merge_output_contract.py` (new) | Test |
| 1 | `tests/unit/test_dag_snapshots.py` (new) | Test |
| 2 | `dalston/common/transcript_ops.py` (new) | Production |
| 2 | `dalston/common/pii_ops.py` (new) | Production |
| 2 | `dalston/common/audio_ops.py` (new) | Production |
| 2 | `engines/stt-merge/final-merger/engine.py` | Production |
| 3 | `dalston/common/pipeline_types.py` | Production |
| 3 | `tests/unit/test_pipeline_types.py` | Test |
| 4 | `engines/stt-merge/final-merger/engine.py` | Production |
| 4 | `tests/unit/test_merge_output_contract.py` | Test |
| 5 | `dalston/orchestrator/handlers.py` | Production |
| 5 | `dalston/orchestrator/dag.py` | Production |
| 5 | `engines/stt-merge/final-merger/engine.py` | Production |
| 5 | `tests/unit/test_dag.py` | Test |
| 5 | `tests/integration/test_capability_driven_dag.py` | Test |
| 6 | `dalston/common/pipeline_types.py` | Production |
| 6 | Docs | Documentation |

## Estimated Commits: ~16 (one per step)
