# M54: Event DLQ and Poison-Pill Isolation (Clean-Cut)

| | |
|---|---|
| **Goal** | Eliminate infinite durable-event replay loops by enforcing retry ceilings and dead-letter isolation in the orchestrator event stream |
| **Duration** | 3-5 days |
| **Dependencies** | M33 (Reliable Task Queues), current durable-events stream path |
| **Deliverable** | Redis Streams DLQ (`dalston:events:dlq`), delivery-count-aware failure policy, immediate quarantine for malformed events, and legacy replay cleanup |
| **Status** | Complete (2026-03-05) |

## Desired Outcomes

### Functional outcomes

1. A poison event no longer replays forever in `dalston:events:stream`.
2. Any event that exceeds max attempts is moved to `dalston:events:dlq` and ACKed in the primary stream.
3. Malformed events (invalid JSON/schema) are quarantined immediately instead of cycling.
4. Healthy events continue processing even when poisoned events exist.

### Operational outcomes

1. Retry and DLQ decisions are visible in logs and metrics.
2. Operators can inspect DLQ entries with full failure context (message ID, reason, delivery count, payload/raw fields).
3. Startup and periodic stale-claim behavior remains deterministic and bounded.

### Clean-start outcomes

1. No compatibility support for pre-M54 infinite-replay behavior.
2. No mixed legacy/new failure policy branches.
3. Legacy helper code left over from old replay assumptions is removed in the same milestone.

### Success criteria

1. `delivery_count >= max_deliveries` always results in loss-averse DLQ transfer: `XADD` to DLQ first, then `XACK` in the main stream.
2. Invalid payload events are not silently skipped; they are moved to DLQ with reason metadata.
3. Unknown event types are no longer silently ACKed; they are classified as non-retryable and routed to DLQ.
4. A synthetic poison event cannot create an endless startup replay loop.
5. New unit/integration tests cover retry, DLQ transfer, malformed events, and healthy-event non-regression.
6. Specs/docs are updated to describe the new reliability contract and operational runbook.

---

## Strategy To Reach Outcomes

### Strategy 1: Freeze one failure policy before implementation

Define one canonical classification model:

1. **Non-retryable**: invalid payload JSON, schema violations, unknown event type.
2. **Retryable**: handler/engine_id exceptions while dispatching valid events.

This prevents ad hoc retry behavior by individual handlers.

### Strategy 2: Make delivery count first-class in event consumption

Use Redis `times_delivered` metadata as the single retry source of truth.

Rules:

1. `read_new_events` (`XREADGROUP ... >`) treats new deliveries as `delivery_count=1` by contract.
2. `claim_stale_pending_events` (`XAUTOCLAIM`) uses returned `times_delivered` metadata; if unavailable, use explicit `XPENDING` lookup fallback.
3. Do not infer retries from logs or timestamps.

### Strategy 3: Centralize decision logic in orchestrator event processing

The orchestrator decides `retry` vs `DLQ` in one shared path used by both live consumption and stale replay.

### Strategy 4: Use loss-averse DLQ move ordering

M54 does not assume crash-proof cross-operation atomicity for DLQ transfer.

Required ordering:

1. Write to DLQ first (`XADD`).
2. ACK source entry second (`XACK`).

This ordering favors no-loss semantics. Duplicate DLQ entries are acceptable; lost events are not.

### Strategy 5: Clean-cut rollout only

No legacy compatibility shims for old event-processing behavior. Code, tests, and docs are updated in one cut.

### Strategy 6: Explicitly document behavior deltas from current code

M54 intentionally changes unknown-event handling from silent ACK to non-retryable DLQ routing.

---

## What Not To Do

1. Do not keep unbounded replay behavior as a fallback.
2. Do not silently drop malformed events with log-only handling.
3. Do not add temporary feature flags for old/new retry behavior.
4. Do not implement per-handler custom retry rules in M54.
5. Do not leave obsolete replay helpers/comments for later cleanup.

---

## Tactical Plan

### Phase 0: Freeze M54 Reliability Contract

1. Define and lock retry policy inputs:
   - `DALSTON_EVENTS_MAX_DELIVERIES` (default `5`)
   - `DALSTON_EVENTS_DLQ_STREAM` (default `dalston:events:dlq`)
   - `DALSTON_EVENTS_DLQ_MAXLEN` (default `10000`, using `MAXLEN ~` on DLQ writes)
2. Define DLQ entry schema:
   - `source_stream`, `source_group`, `source_message_id`, `event_type`
   - `failure_reason`, `error`, `delivery_count`, `consumer_id`, `failed_at`
   - `payload` (when parseable) and `raw_fields`/`raw_payload` for malformed events
3. Freeze failure reason taxonomy (`invalid_payload_json`, `invalid_event_schema`, `unknown_event_type`, `handler_exception`, `dispatch_error`).
4. Freeze explicit behavior delta: unknown event types now route to DLQ instead of silent ACK.

Expected files:

- `dalston/config.py`
- `docs/specs/batch/ORCHESTRATOR.md`

### Phase 1: Add Durable-Event Metadata + DLQ Primitives

1. Introduce a typed durable-event envelope as a **Pydantic model** carrying:
   - message ID
   - parsed payload (if valid)
   - raw fields
   - `delivery_count`
   - parse/validation error metadata
2. Update `read_new_events` and `claim_stale_pending_events` to surface delivery counts and parse status:
   - `read_new_events`: emit `delivery_count=1` for first-delivery events from `XREADGROUP ... >`
   - `claim_stale_pending_events`: emit real `delivery_count` from `XAUTOCLAIM`/`XPENDING`
3. Add DLQ write helper and loss-averse move helper (`XADD` DLQ first, then `XACK` source).
4. Add stream info helper for DLQ length/visibility.

Expected files:

- `dalston/common/durable_events.py`
- `tests/unit/test_durable_events.py`

### Phase 2: Enforce Retry Ceiling in Orchestrator Event Loop

1. Replace duplicated live/replay failure handling with one shared decision path.
2. On successful dispatch: ACK in primary stream.
3. On failure:
   - non-retryable reason -> DLQ immediately + ACK
   - retryable and `delivery_count < max` -> leave pending
   - retryable and `delivery_count >= max` -> DLQ + ACK
4. Ensure startup replay and periodic stale claim both use the same policy.
5. Route `unknown_event_type` through non-retryable DLQ path (no silent ACK path retained).

Expected files:

- `dalston/orchestrator/main.py`
- `tests/unit/test_orchestrator_event_reliability.py` (new)

### Phase 3: Observability and Ops Runbook

1. Add metrics for retry and DLQ routing decisions.
2. Add structured logs including `message_id`, `delivery_count`, `failure_reason`, and `decision`.
3. Add documented Redis inspection commands for DLQ triage:
   - `redis-cli XRANGE dalston:events:dlq - + COUNT 10`
   - `redis-cli XREVRANGE dalston:events:dlq + - COUNT 20`
4. Define DLQ retention policy:
   - bounded with `MAXLEN ~ DALSTON_EVENTS_DLQ_MAXLEN`
   - no automatic replay in M54
5. Define replay policy for M54:
   - manual replay only (operator-driven), after root-cause correction
   - no automatic DLQ->main-stream requeue loop in this milestone

Expected files:

- `dalston/metrics.py`
- `docs/specs/OBSERVABILITY.md`

### Phase 4: Legacy Code Removal (Required)

1. Remove obsolete `read_pending_events` helper from `durable_events.py` and its tests as part of the clean-cut.
2. Remove silent-skip parsing branches that hide poison events from policy enforcement.
3. Remove comments/docs that describe infinite replay as expected behavior.
4. Remove temporary compatibility branches introduced during implementation before merge.

Expected files:

- `dalston/common/durable_events.py`
- `tests/unit/test_durable_events.py`
- `dalston/orchestrator/main.py`
- related docs

### Phase 5: Documentation and Spec Completion

1. Update orchestrator reliability spec with DLQ semantics and retry ceiling.
2. Update observability spec with new counters and runbook steps.
3. Update plan/docs index to include M54 and mark status.
4. Add short implementation report summarizing behavior changes and operational guidance.

Expected files:

- `docs/specs/batch/ORCHESTRATOR.md`
- `docs/specs/OBSERVABILITY.md`
- `docs/plan/README.md`
- `docs/README.md`
- `docs/reports/M54-event-dlq-implementation.md` (new)

---

## Testing Plan

### Automated tests

1. Durable events unit tests:
   - `delivery_count=1` contract for `read_new_events` (`XREADGROUP ... >`)
   - real delivery count surfaced for claimed events (`XAUTOCLAIM`/`XPENDING`)
   - malformed payloads produce explicit non-retryable classification
   - DLQ move writes required metadata and enforces `XADD`-before-`XACK` ordering
2. Orchestrator unit tests:
   - retryable failure below threshold remains pending
   - retryable failure at threshold moves to DLQ and ACKs
   - malformed event routes to DLQ on first handling attempt
   - unknown event type routes to DLQ (behavior delta from silent ACK)
   - healthy event still ACKs normally
3. Integration tests (Redis-backed):
   - poison event exceeds threshold and lands in DLQ
   - poisoned event no longer appears as pending in primary stream after DLQ move
   - healthy events continue processing while poison event is quarantined
   - restart path does not loop endlessly on the same poison message

Suggested command set:

```bash
pytest \
  tests/unit/test_durable_events.py \
  tests/unit/test_orchestrator_event_reliability.py \
  tests/integration/test_event_dlq_recovery.py -q
```

### Manual verification

1. Inject a malformed durable event directly into `dalston:events:stream` and verify immediate DLQ routing.
2. Inject a valid event whose handler is forced to fail repeatedly and verify DLQ routing at configured max deliveries.
3. Confirm with Redis inspection:
   - main stream pending entry is ACKed after DLQ transfer
   - DLQ stream contains failure metadata and original payload context
4. Verify DLQ retention behavior (`MAXLEN ~`) under repeated poison injections.
5. Restart orchestrator and verify it starts cleanly without replaying quarantined poison events.

---

## Risks and Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| Incorrect delivery-count reads cause premature DLQ or extra retries | High | Unit tests against Redis response shapes + integration validation with real stream states |
| Event loss during DLQ transfer due to wrong operation ordering | High | Enforce `XADD` before `XACK`; add tests that assert no-loss semantics and allow duplicate DLQ entries |
| Overly aggressive non-retryable classification | Medium | Keep taxonomy minimal and explicit; document reason mapping |
| Legacy replay code remains and bypasses policy | Medium | Make legacy cleanup an exit gate, not follow-up work |

---

## Exit Criteria

1. Orchestrator event handling enforces a max delivery threshold with DLQ routing.
2. Malformed or unknown events are quarantined, not replayed indefinitely.
3. No obsolete infinite-replay compatibility code remains in the event path.
4. Tests cover live consumption, stale replay, and non-regression for healthy events.
5. Docs/specs describe the final behavior and DLQ operations.
