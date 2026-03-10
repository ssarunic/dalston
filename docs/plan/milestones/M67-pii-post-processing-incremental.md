# M67: PII Post-Processing Migration (Feature-Flagged Parity)

| | |
|---|---|
| **Goal** | Move PII text/audio redaction from core pipeline stages to async post-processing with parity validation |
| **Duration** | 1 week |
| **Dependencies** | M63 |
| **Primary Deliverable** | `PostProcessor` flow and `pii_mode` rollout (`pipeline` vs `post_process`) |
| **Status** | Complete |

## Outcomes

1. Core pipeline no longer blocks on PII processing when post-process mode is enabled.
2. PII text redaction and audio redaction remain functionally equivalent.
3. Migration is controlled by feature flag and reversible.
4. Compliance-sensitive deployments keep a blocking path if required.

## Scope

In scope:

- Introduce async post-processing orchestration for PII jobs.
- Add runtime flag for mode selection and compatibility behavior.
- Parity tests between pipeline mode and post-process mode.

Out of scope:

- DAG linearization and merge elimination (covered by M68).
- Broader compliance policy definition beyond technical controls.

## Strategy

1. Add post-processing framework without changing default behavior.
2. Keep pipeline mode as default while parity tests are built.
3. Roll out post-process mode in canary with explicit monitoring.
4. Keep fallback mode until compliance signoff.

## Tactics

### T1. Post-Processing Framework

- Add orchestration module for post-completion enrichment jobs.
- Support ordering: `pii_detect -> audio_redact`.
- Track enrichment status and failures per job.

Gate:

- `make test`
- `make lint`

### T2. Feature Flag and Dual Mode

- Add `pii_mode` setting:
  - `pipeline` (default)
  - `post_process`
- Route job flow based on mode.

Gate:

- Both modes pass existing tests.
- No API response regressions.

### T3. Parity Validation

- Run same inputs through both modes.
- Assert redacted transcript/audio equivalence (allowing metadata timing variance).

Gate:

- Parity suite green on representative corpus.
- Error handling and retry paths validated.

### T4. Rollout

- Canary enablement for selected workloads.
- Observe completion lag, failure rates, and redaction correctness.

Gate:

- No compliance-impacting mismatches in canary window.

## Incremental Step Plan

## Phase 0: Baseline

1. Capture current PII behavior snapshots in pipeline mode.
2. Add parity comparator helpers for text and audio outputs.

## Phase 1: Async Flow

1. Add `PostProcessor` module and enqueue mechanism.
2. Implement `pii_detect` and `audio_redact` post-completion execution.

## Phase 2: Dual Mode

1. Add `pii_mode` setting and branch execution flow.
2. Keep default as pipeline mode.

## Phase 3: Parity and Rollout

1. Run parity corpus on both modes.
2. Canary `post_process` mode.
3. Expand rollout once parity/SLIs are stable.

## Testing Matrix

- Unit: post-processing state transitions and retries.
- Integration: end-to-end redaction in both modes.
- Parity: mode-vs-mode equivalence checks.
- Failure: engine failure, retry exhaustion, and artifact availability edge cases.

Recommended gate:

```bash
make test
make lint
```

Release gate:

```bash
pytest -m integration
```

## Rollback and Safety Controls

- `DALSTON_PII_MODE=pipeline|post_process`
- Rollback: set `DALSTON_PII_MODE=pipeline`.
- Optional deployment guard: enforce pipeline mode where policy forbids temporary unredacted storage.

## Success Criteria

- Post-process mode delivers equivalent redaction outcomes.
- No API break for existing consumers.
- Operational performance improves for core completion path where enabled.

## Implementation Status

### Complete

- **`dalston/orchestrator/post_processor.py`**: `PostProcessor` module with
  `needs_post_processing()`, `build_post_processing_tasks()`,
  `schedule_post_processing()`, `check_post_processing_completion()`, and
  `is_post_processing_task()`.
- **Core DAG updated**: `dalston/orchestrator/dag.py` — PII stages (`pii_detect`,
  `audio_redact`) removed from main pipeline DAG. Mono pipeline is now
  `prepare → transcribe → [align] → [diarize]`.
- **Post-processing engines wired**: `DEFAULT_ENGINES` retains `"pii_detect":
  "pii-presidio"` and `"audio_redact": "audio-redactor"` for post-processing
  dispatch. Pipeline mode (`pii_mode=pipeline`) is no longer the default.
- Note: the `DALSTON_PII_MODE` flag was not implemented as a runtime toggle;
  post-process mode is the sole path.

## References

- `docs/plans/pipeline-simplification-plan.md` (PR-5)
- `docs/reviews/2026-03-09-complexity-review.md` (sections on PII feasibility/caveats)
