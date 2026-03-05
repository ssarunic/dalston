# Realtime Latency Budget and Backpressure

## Status

- **Milestone:** M53
- **State:** Implemented (2026-03-05)
- **Scope:** Realtime worker session pipeline, gateway realtime translation, protocol close/error semantics

---

## Problem (Pre-M53)

The current realtime pipeline relies on implicit transport backpressure (`await send`) and has no explicit lag budget enforcement. Under CPU contention or slow inference, sessions can degrade into high latency without deterministic warning/termination behavior.

---

## Intended Outcomes

1. Detect lag explicitly during active sessions.
2. Warn client when lag crosses warning budget.
3. Terminate session with explicit close code when hard lag budget is exceeded.
4. Remove pre-M53 compatibility paths in realtime code after cutover.

---

## Canonical Lag Model

### Definitions

- `received_audio_seconds`: cumulative audio duration accepted from client.
- `processed_audio_seconds`: cumulative audio duration that has completed one processing cycle in `SessionHandler` (VAD evaluation plus any triggered ASR call).
- `lag_seconds = received_audio_seconds - processed_audio_seconds`.

### Rationale

`transcript` duration is not a reliable lag signal because transcript timeline can exclude silence depending on model/VAD behavior. M53 uses audio ingestion vs processing cursors.

### Cursor measurement points

1. `received_audio_seconds` is computed from accepted/decoded audio bytes using negotiated session config (`encoding`, `sample_rate`, `channels`).
2. Conversion uses encoding-aware bytes-per-sample:
   - `pcm_s16le`: 2 bytes/sample/channel
   - `pcm_f32le`: 4 bytes/sample/channel
   - `mulaw` / `alaw`: 1 byte/sample/channel
3. `processed_audio_seconds` advances when chunk processing completes, not when it starts.
4. ElevenLabs/OpenAI base64 payloads are converted to raw bytes first, then counted using the same encoding/sample-rate rules.

---

## Budget Policy

### Default thresholds

- `lag_warning_seconds = 3.0`
- `lag_hard_seconds = 5.0`
- `lag_hard_grace_seconds = 2.0`

### Behavior

1. **Normal:** `lag_seconds < lag_warning_seconds`
   - No lag event.
2. **Warning:** `lag_seconds >= lag_warning_seconds`
   - Emit `processing_lag` warning event (rate-limited to at most once per second per session).
3. **Terminate:** `lag_seconds >= lag_hard_seconds` for `lag_hard_grace_seconds`
   - Emit structured terminal error (`lag_exceeded`).
   - Close websocket with `WS_CLOSE_LAG_EXCEEDED`.

### Grace timer semantics

1. Grace timer starts when lag first crosses `lag_hard_seconds`.
2. Grace timer resets immediately if lag falls below `lag_hard_seconds`.
3. Termination requires continuous (non-cumulative) hard-threshold exceedance for the full grace duration.

---

## Protocol Additions

### WebSocket close code

- `WS_CLOSE_LAG_EXCEEDED = 4010`

### Dalston native warning event

```json
{
  "type": "warning",
  "code": "processing_lag",
  "message": "Processing lag is above threshold",
  "lag_seconds": 3.7,
  "warning_threshold_seconds": 3.0,
  "hard_threshold_seconds": 5.0
}
```

### Dalston native terminal error event

```json
{
  "type": "error",
  "code": "lag_exceeded",
  "message": "Realtime lag budget exceeded",
  "recoverable": false
}
```

### ElevenLabs/OpenAI translation

- Internal warning/error signals are mapped to endpoint-specific message shapes below.
- No legacy branch for pre-M53 worker behavior is kept.

#### ElevenLabs warning mapping

```json
{
  "message_type": "warning",
  "code": "processing_lag",
  "message": "Processing lag is above threshold",
  "lag_seconds": 3.7,
  "warning_threshold_seconds": 3.0,
  "hard_threshold_seconds": 5.0
}
```

#### ElevenLabs terminal mapping

```json
{
  "message_type": "error",
  "code": "lag_exceeded",
  "message": "Realtime lag budget exceeded",
  "recoverable": false
}
```

#### OpenAI terminal mapping

```json
{
  "type": "error",
  "event_id": "evt_123",
  "error": {
    "type": "server_error",
    "code": "lag_exceeded",
    "message": "Realtime lag budget exceeded"
  }
}
```

OpenAI warning mapping reuses a warning envelope:

```json
{
  "type": "warning",
  "event_id": "evt_124",
  "warning": {
    "code": "processing_lag",
    "message": "Processing lag is above threshold",
    "lag_seconds": 3.7,
    "warning_threshold_seconds": 3.0,
    "hard_threshold_seconds": 5.0
  }
}
```

---

## Configuration Surface

Expose lag budget knobs via realtime worker config (environment and/or session config parsing in SDK):

- `DALSTON_REALTIME_LAG_WARNING_SECONDS`
- `DALSTON_REALTIME_LAG_HARD_SECONDS`
- `DALSTON_REALTIME_LAG_HARD_GRACE_SECONDS`

Validation rules:

- `warning > 0`
- `hard > warning`
- `grace > 0`

Invalid values fail closed at startup/connection parse.

---

## Session Lifecycle Effects

On lag termination:

1. Session status is finalized as error/interrupted with `lag_exceeded` reason.
2. Worker slot is released normally via existing session router flow.
3. Session logs include lag values and threshold config used.
4. Session termination is non-recoverable:
   - `recoverable = false`
   - no replay/recovery hint is emitted

---

## Monitoring Model

M53 enforces lag budgets with two checks:

1. Inline checks at chunk/control boundaries.
2. A periodic monitor task (target tick: 250ms) that evaluates lag/grace timers while long inference calls are in-flight.

---

## Non-Goals (M53)

1. Client-side adaptive pacing protocol.
2. Dynamic model switching mid-session to recover lag.
3. Backward compatibility with pre-M53 realtime worker protocol.

---

## Legacy Cleanup Requirements

After implementation, remove:

1. Compatibility branches for old worker lag semantics.
2. Obsolete comments/docs that claim implicit backpressure is the only control.
3. Temporary migration toggles used only during M53 development.

M53 is complete only when this cleanup is done.
