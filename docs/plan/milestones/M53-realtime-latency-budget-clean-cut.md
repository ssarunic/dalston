# M53: Realtime Latency Budget and Explicit Backpressure (Clean-Cut)

| | |
|---|---|
| **Goal** | Guarantee bounded realtime lag with explicit warning and termination behavior under engine slowdown |
| **Duration** | 3-5 days |
| **Dependencies** | M6 (Realtime MVP), M8 (ElevenLabs compatibility) |
| **Deliverable** | Lag budget enforcement in `SessionHandler`, protocol warning event, explicit lag close code, clean-cut removal of legacy compatibility paths |
| **Status** | Complete (2026-03-05) |

## Desired Outcomes

### Functional outcomes

1. Sessions never continue indefinitely with severe processing delay.
2. The system tracks lag explicitly during live streaming and reacts deterministically.
3. Clients receive a structured `processing_lag` warning before hard termination.
4. Sessions that exceed hard lag budget close with a dedicated WebSocket close code.

### Operational outcomes

1. Lag behavior is observable and testable in unit/integration tests.
2. Gateway/session logs explain why a session was warned or terminated.
3. Realtime behavior remains stable under CPU contention and slow-model scenarios.

### Clean-start outcomes

1. No compatibility support for pre-M53 realtime worker behavior.
2. No dual protocol branches for old/new lag semantics.
3. Legacy code introduced for mixed-version rollouts is removed before merge.

### Success criteria

1. Lag warning is emitted when lag crosses warning threshold (default: 3.0s).
2. Session is closed when lag exceeds hard threshold (5.0s) sustained for the grace window (2.0s).
3. Hard close uses `WS_CLOSE_LAG_EXCEEDED` and a structured error payload.
4. New tests cover warning, termination, and non-regression happy paths.
5. Docs/specs reflect the new contract and remove old behavior language.

---

## Strategy To Reach Outcomes

### Strategy 1: Define lag semantics first, then implement

Freeze one lag definition and thresholds before coding to avoid drift:

- `lag_seconds = received_audio_seconds - processed_audio_seconds`
- `received_audio_seconds`: derived from accepted/decoded audio bytes using negotiated session config (`encoding`, `sample_rate`, `channels`)
- `processed_audio_seconds`: advances only when a chunk's processing cycle completes (VAD evaluation plus any triggered ASR call), so ASR slowdown is visible as lag growth

This intentionally avoids using transcript duration as lag signal.

### Strategy 2: Enforce budget in the worker session loop

Apply lag accounting and policy in `SessionHandler` where audio is actually consumed and transcription blocking occurs. Gateway remains transport/proxy and protocol translation.

### Strategy 3: Deterministic policy over adaptive complexity

For M53, use explicit warning + hard termination as the canonical behavior. Do not rely on implicit TCP backpressure as the primary control mechanism.

### Strategy 4: Hybrid monitoring for deterministic enforcement

Use both:

1. inline lag checks at chunk/control boundaries, and
2. a lightweight periodic monitor task (for long ASR calls)

so grace-window termination is enforceable even during long inference steps.

### Strategy 5: Clean-cut rollout only

No support for old worker protocol or old session behavior. Merge as a single-version cut: code, tests, and docs updated together.

### Strategy 6: Legacy cleanup is a milestone requirement

Treat removal of obsolete branches/comments/helpers as a required exit gate, not follow-up work.

---

## What Not To Do

1. Do not add compatibility shims for older realtime engine/session protocol versions.
2. Do not implement silent fallback to legacy close codes on lag breach.
3. Do not use transcript duration delta as lag signal.
4. Do not add client-side pacing negotiation protocols in M53.
5. Do not leave temporary migration flags or dead branches after cutover.

---

## Tactical Plan

### Phase 0: Freeze M53 lag contract

1. Lock lag formula, thresholds, and event semantics in spec docs.
2. Define close/error semantics:
   - `WS_CLOSE_LAG_EXCEEDED = 4010`
   - `error.code = "lag_exceeded"`
   - lag-exceeded termination is non-recoverable (`recoverable=false`, no replay hint)
3. Freeze grace-window semantics:
   - grace timer starts when lag first crosses hard threshold
   - timer resets immediately if lag drops below hard threshold
   - termination requires continuous (not cumulative) hard-threshold exceedance for full grace window
4. Define warning payload shape for Dalston native and translation behavior for ElevenLabs/OpenAI endpoints, including concrete payload examples.

Expected files:

- `docs/specs/realtime/LATENCY_BUDGET_BACKPRESSURE.md` (new)
- `docs/specs/realtime/WEBSOCKET_API.md`

### Phase 1: Add protocol and close-code primitives

1. Add new close code in shared close-code module.
2. Add new warning/error protocol message shapes for realtime worker output.
3. Add config fields for lag thresholds/grace in session config parsing.

Expected files:

- `dalston/common/ws_close_codes.py`
- `dalston/realtime_sdk/protocol.py`
- `dalston/realtime_sdk/base.py`

### Phase 2: Implement lag accounting and budget enforcement in SessionHandler

1. Track `received_audio_seconds` as audio is accepted.
2. Track `processed_audio_seconds` as chunks are consumed.
3. Evaluate lag both inline and from a periodic monitor task (tick target: 250ms).
4. Emit `processing_lag` warning when warning threshold is crossed, rate-limited to at most once per second per session.
5. Trigger terminal flow when hard threshold is sustained for grace window:
   - send error event
   - close websocket with `WS_CLOSE_LAG_EXCEEDED`
   - emit `session.terminated` with `reason=lag_exceeded`, `recoverable=false`, and no `recovery_hint`
   - mark session as errored with reason `lag_exceeded`

Expected files:

- `dalston/realtime_sdk/session.py`
- `dalston/realtime_sdk/protocol.py`

### Phase 3: Gateway translation and session persistence integration

1. Pass through new warning/error semantics in Dalston native proxy path.
2. Map warning/error semantics for ElevenLabs/OpenAI translation endpoints.
3. Persist lag-exceeded session status/reason in realtime session records/logs.

Expected files:

- `dalston/gateway/api/v1/realtime.py`
- `dalston/gateway/api/v1/openai_realtime.py`
- `dalston/gateway/services/realtime_sessions.py` (if status/error taxonomy needs extension)

### Phase 4: Clean-start removal of legacy code

1. Remove any compatibility branches added for pre-M53 behavior.
2. Remove obsolete comments/docs claiming implicit backpressure is sufficient.
3. Remove dead helper code made obsolete by final lag-budget flow.

Expected files:

- `dalston/realtime_sdk/session.py`
- `dalston/gateway/api/v1/realtime.py`
- `dalston/gateway/api/v1/openai_realtime.py`
- related tests/docs

### Phase 5: Documentation and spec updates

1. Update realtime architecture docs to include explicit lag budget control loop.
2. Update WebSocket API docs with warning payload and lag close code.
3. Add milestone status/links in plan index.

Expected files:

- `docs/specs/realtime/REALTIME.md`
- `docs/specs/realtime/WEBSOCKET_API.md`
- `docs/plan/README.md`
- `docs/plan/milestones/M53-realtime-latency-budget-clean-cut.md`

---

## Testing Plan

### Automated tests

1. Unit tests for lag accounting math in `SessionHandler`:
   - lag stays near zero when engine keeps up
   - lag grows when `transcribe_fn` is intentionally delayed
2. Threshold behavior tests:
   - warning emitted once threshold crossed
   - warning rate-limit respected (no event flood at high eval frequency)
   - hard-threshold grace timer resets when lag dips below hard threshold
   - termination triggered only after hard threshold + grace
3. Protocol tests:
   - warning payload schema
   - `lag_exceeded` error payload schema
   - close code value assertions
4. Gateway translation tests:
   - Dalston native path forwards warning/error
   - ElevenLabs/OpenAI translation emits expected mapped events
5. Regression tests:
   - standard realtime session still succeeds without lag breach
   - no false lag warning in normal conditions

Suggested command set:

```bash
pytest \
  tests/unit/test_realtime_protocol.py \
  tests/unit/test_realtime_session_config.py \
  tests/unit/test_realtime_lag_budget.py \
  tests/integration/test_realtime_e2e.py \
  tests/integration/test_realtime_lag_guardrails.py -q
```

### Manual verification

1. Run a realtime worker with intentionally slow transcription (CPU or injected sleep).
2. Stream live audio and confirm:
   - warning event appears after warning threshold
   - session closes with lag-exceeded semantics at hard threshold
3. Verify session final state/logs in gateway and session persistence records.

---

## Risks and Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| False positives due to poor lag signal | High | Use received-vs-processed audio lag, not transcript duration |
| Behavior mismatch across Dalston/ElevenLabs/OpenAI endpoints | Medium | Add translation tests and one canonical internal warning/error contract |
| Remaining dead compatibility branches after cutover | Medium | Include explicit legacy cleanup phase and exit criteria gate |
| Overly aggressive thresholds cause unnecessary disconnects | Medium | Use defaults + grace window; make thresholds configurable |

---

## Exit Criteria

1. Explicit lag budget is enforced in `SessionHandler`.
2. `processing_lag` warning and `lag_exceeded` terminal behavior are implemented and tested.
3. Dedicated lag close code is used for hard termination.
4. No pre-M53 compatibility branches remain in realtime path.
5. Docs/specs reflect M53 behavior and remove obsolete implicit-backpressure language.
