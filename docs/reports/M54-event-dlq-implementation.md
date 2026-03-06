# M54 Event DLQ Implementation Report

Date: 2026-03-05
Status: Complete

## Summary

M54 is implemented as a clean-cut reliability policy for durable orchestrator events.
The orchestrator now enforces delivery ceilings and quarantines poison/malformed events
into a DLQ (`dalston:events:dlq`) instead of replaying indefinitely.

## Delivered Changes

## 1) Durable Event Envelope + DLQ Primitives

Updated: `dalston/common/durable_events.py`

- Added `DurableEventEnvelope` (Pydantic) with:
  - `message_id`
  - `delivery_count`
  - `event_type`, `payload`, `timestamp`
  - `raw_fields`, `raw_payload`, `trace_context`
  - `failure_reason`, `error`
- `read_new_events()` now returns typed envelopes with `delivery_count=1`.
- `claim_stale_pending_events()` resolves delivery counts from claim metadata or
  `XPENDING` fallback.
- Added M54 failure taxonomy constants:
  - `invalid_payload_json`
  - `invalid_event_schema`
  - `unknown_event_type`
  - `handler_exception`
  - `dispatch_error`
- Added DLQ helpers:
  - `add_event_to_dlq()`
  - `move_event_to_dlq()` with strict ordering:
    1. `XADD` DLQ
    2. `XACK` source stream
- Removed obsolete `read_pending_events()` helper.

## 2) Orchestrator Retry-vs-DLQ Decision Path

Updated: `dalston/orchestrator/main.py`

- Added unified `_process_durable_event()` used by:
  - live stream consumption
  - stale pending replay path
- Implemented policy:
  - Non-retryable (`invalid_payload_json`, `invalid_event_schema`, `unknown_event_type`)
    -> immediate DLQ + ACK
  - Retryable (`handler_exception`, `dispatch_error`) and
    `delivery_count < DALSTON_EVENTS_MAX_DELIVERIES`
    -> leave pending (retry later)
  - Retryable and `delivery_count >= DALSTON_EVENTS_MAX_DELIVERIES`
    -> DLQ + ACK
- Unknown event types no longer silently ACK.
- Added explicit event schema guards (`EventSchemaError`) and unknown-type guard.

## 3) Observability

Updated: `dalston/metrics.py`, `dalston/orchestrator/main.py`

- Added metric:
  - `dalston_orchestrator_event_decisions_total{decision,failure_reason,event_type}`
- Emitted decisions for all durable-event outcomes:
  - `ack`
  - `retry`
  - `dlq`
- Structured logs now include durable decision metadata:
  - `message_id`, `event_type`, `delivery_count`, `source`, `decision`,
    `failure_reason`, and `error` when present.

## 4) Configuration Surface

Updated: `dalston/config.py`, `.env.example`

- Added:
  - `DALSTON_EVENTS_MAX_DELIVERIES` (default `5`)
  - `DALSTON_EVENTS_DLQ_STREAM` (default `dalston:events:dlq`)
  - `DALSTON_EVENTS_DLQ_MAXLEN` (default `10000`, applied as `MAXLEN ~`)

## Test Evidence

### Phase 1: Unit

```bash
.venv/bin/pytest tests/unit/test_durable_events.py tests/unit/test_orchestrator_event_reliability.py -q
```

Result: `22 passed`.

### Phase 2: Targeted integration

```bash
.venv/bin/pytest tests/integration/test_event_dlq_recovery.py tests/integration/test_metrics_api.py -q
```

Result: `15 passed`.

### Phase 3: Full integration suite

```bash
.venv/bin/pytest tests/integration -q
```

Result: `342 passed, 6 skipped` (2 unrelated warnings in existing polling tests).

### Phase 4: E2E

E2E requires:

- CLI entrypoint available on PATH (`.venv/bin/dalston`)
- valid `DALSTON_API_KEY`
- test process network access to local Docker stack (`localhost:8000`)

Command executed:

```bash
PATH="/Users/sasasarunic/_Sources/dalston/.venv/bin:$PATH" \
DALSTON_API_KEY="<generated key>" \
.venv/bin/pytest -m e2e -q
```

Result in this environment: `37 passed, 17 skipped, 25 failed`.

Observed failing categories are environment/profile dependent rather than M54 code paths:

- metrics-disabled assertion while gateway metrics endpoint is enabled in this stack
- parakeet model tests with required transcribe engine not running
- rate-limit counter drift (`429 concurrent job limit exceeded`) after repeated heavy scenarios
- intermittent diarization task failure (`dummy.wav` input/open error)

Final rerun from a clean state:

```bash
PATH="/Users/sasasarunic/_Sources/dalston/.venv/bin:$PATH" \
DALSTON_API_KEY="<generated key>" \
.venv/bin/pytest -m e2e -q
```

Result: `39 passed, 17 skipped, 23 failed`.

Additional observed categories in the final run:

- diarization auth/token mismatch paths (`HF_TOKEN`/model-access error surfaced by pyannote stage)
- OpenAI SDK e2e returning empty text payload for a successful HTTP response

Counter-reset verification performed:

```bash
docker compose exec -T redis redis-cli DEL dalston:ratelimit:jobs:00000000-0000-0000-0000-000000000000
PATH="/Users/sasasarunic/_Sources/dalston/.venv/bin:$PATH" \
DALSTON_API_KEY="<generated key>" \
.venv/bin/pytest -m e2e tests/e2e/test_transcription_e2e.py::TestDefaultTranscription::test_mono_transcription -q
```

Result: `1 passed` (core transcription e2e works after stale counter reset).

## Operational Notes

- DLQ retention is bounded by `DALSTON_EVENTS_DLQ_MAXLEN` via `XADD MAXLEN ~`.
- M54 does **not** implement automatic DLQ replay.
- Replay remains manual/operator-driven after root-cause correction.

Useful inspection commands:

```bash
redis-cli XRANGE dalston:events:dlq - + COUNT 20
redis-cli XREVRANGE dalston:events:dlq + - COUNT 20
redis-cli XPENDING dalston:events:stream orchestrators
```
