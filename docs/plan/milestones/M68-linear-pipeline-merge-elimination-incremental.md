# M68: Linear Core Pipeline + Merge Elimination (Last Core Refactor)

| | |
|---|---|
| **Goal** | Replace fork-join/merge complexity with a mostly linear core pipeline after unification milestones are stable |
| **Duration** | 2 weeks |
| **Dependencies** | M63, M64, M66, M67 |
| **Primary Deliverable** | Core flow `prepare -> transcribe -> [align] -> [diarize]` with merge removed and deterministic terminal stage handling |
| **Status** | Complete — merge eliminated from both mono and per-channel pipelines |

## Outcomes

1. Core pipeline complexity is reduced by removing merge fan-in behavior.
2. `align` remains a capability-gated fallback stage (not removed).
3. PII remains post-processing (from M67), outside core stage wiring.
4. Final transcript selection is deterministic and independent of completion timing.

## Scope

In scope:

- Introduce/validate shared transcript handoff model for core stages.
- Remove merge usage for mono core path once parity is proven.
- Enforce deterministic terminal-stage output resolution.
- Preserve optional align execution based on transcriber capability.

Out of scope:

- per_channel redesign (explicitly last/optional; separate effort).
- Declarative `engine.yaml` pipeline auto-wiring and new stages.

## Strategy

1. Introduce additive transcript model and adapters first.
2. Migrate core stages incrementally to enrich one shared transcript.
3. Keep dual-path execution until parity is proven.
4. Remove merge only after canary confidence.

## Tactics

### T1. Additive Shared Transcript Model

- Define canonical transcript schema and conversion from current stage outputs.
- Keep old outputs valid during transition.

Gate:

- `make test`
- `make lint`

### T2. Deterministic Pipeline Terminal Handling

- Explicitly record terminal stage name in pipeline definition.
- Do not rely on "last completed task" heuristics.

Gate:

- Retry/reorder tests verify deterministic final output source.

### T3. Stage-by-Stage Migration

- Transcribe outputs transcript model.
- Align enriches transcript only when included.
- Diarize enriches transcript with speaker assignments.

Gate:

- Per-stage parity tests against legacy merge output.

### T4. Merge Decommission (Mono Path)

- Omit merge in eligible mono pipelines.
- Keep compatibility path behind feature flag during rollout.

Gate:

- Parity and export tests pass (`SRT`/`VTT`/`TXT`).
- Canary non-regression in transcript quality and job success rates.

### T5. Cleanup

- Remove merge engine wiring and related dependency fan-in logic.
- Delay schema cleanup (`task_dependencies`) until post-stabilization window.

Gate:

- No in-flight jobs on legacy path before irreversible cleanup.

## Incremental Step Plan

## Phase 0: Baseline

1. Add parity tests comparing legacy merge output with shared transcript projection.
2. Add terminal-stage determinism tests under retries/restarts.

## Phase 1: Additive Model

1. Introduce shared transcript model and conversion helpers.
2. Keep merge path as production default.

## Phase 2: Core Stage Migration

1. Move transcribe output to transcript model.
2. Add optional align enrichment path (capability-gated).
3. Move diarize enrichment into transcript path.

## Phase 3: Dual Path Rollout

1. Enable linear core path behind feature flag.
2. Run parity canary and compare outputs/exports.

## Phase 4: Merge Removal (Core Mono Path)

1. Remove merge from default mono path after stable window.
2. Keep guarded rollback path until full confidence.

## Testing Matrix

- Unit: transcript model transforms and stage enrichments.
- Integration: full job pipeline with/without align.
- Compatibility: transcript/export parity vs legacy outputs.
- Resilience: retries/restarts maintain deterministic final output.

Recommended gate:

```bash
make test
make lint
```

Release gate:

```bash
pytest -m integration
pytest -m e2e
```

## Rollback and Safety Controls

- `DALSTON_LINEAR_PIPELINE_ENABLED=true|false`
- `DALSTON_MERGE_ENGINE_ENABLED=true|false`
- Rollback: disable linear path and re-enable merge path.

## Success Criteria

- Merge is removed from core mono pipeline without output regressions.
- Align still runs where native timestamp precision is insufficient.
- Operational complexity and failure surface of DAG/merge flow is reduced.

## Implementation Status

### Complete

**Mono pipeline:** `dalston/orchestrator/dag.py` — the default mono
pipeline is `prepare → transcribe → [align] → [diarize]` with no merge stage.
The orchestrator assembles `transcript.json` on job completion directly from
stage outputs.

**Per-channel pipeline:** Per-channel pipelines no longer use a merge stage.
The DAG is `prepare → transcribe_ch0 → [align_ch0] / transcribe_ch1 → [align_ch1]`.
The orchestrator assembles `transcript.json` using `assemble_per_channel_transcript`
in `dalston/common/transcript.py`, which collects segments from each channel,
assigns `SPEAKER_XX` IDs based on channel index, and interleaves segments by
start time.

Feature flags `DALSTON_LINEAR_PIPELINE_ENABLED` and
`DALSTON_MERGE_ENGINE_ENABLED` were not added; linear behavior is
hardcoded for all pipeline shapes.

## References

- `docs/plans/pipeline-simplification-plan.md` (PR-7, PR-8, PR-10, PR-11 ordering)
- `docs/reviews/2026-03-09-complexity-review.md`
- `docs/plan/milestones/M67-pii-post-processing-incremental.md`
