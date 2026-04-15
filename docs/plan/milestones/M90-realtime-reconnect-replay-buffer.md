# M90: Realtime WebSocket Reconnect with Replay Buffer

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | A dropped WebSocket during a live realtime session reconnects cleanly, replays audio that wasn't acknowledged, and returns to streaming without lost words or duplicate partials |
| **Duration**       | 5–7 days                                                     |
| **Dependencies**   | M24 (Realtime Session Persistence — complete, provides `resume_session_id` and DB-backed session state) |
| **Deliverable**    | Client-side ring buffer, server ACK protocol, reconnect handshake extension, audio replay on reattach, integration tests |
| **Status**         | Not Started                                                  |

## User Story

> *"As a voice agent running on a flaky mobile connection, when my WebSocket drops mid-utterance I want the SDK to reconnect within 2 seconds, replay the last second of audio I sent but never got ACKed, and continue streaming — without losing the words the user was in the middle of saying, and without the backend delivering me duplicate partial transcripts."*

---

## Outcomes

| Scenario | Current | After M90 |
| -------- | ------- | --------- |
| WebSocket drops mid-utterance due to network hiccup (not server-side) | Session ends, audio buffered client-side is lost, client must start a fresh session. `resume_session_id` is accepted by the gateway but the actual reattach-and-replay flow is not implemented end-to-end | Client SDK reconnects with `resume_session_id`, replays un-ACKed audio from its ring buffer, server resumes VAD/worker state from the last commit |
| `livekit/agents#4609` repro: STT stream raises `retryable=True` but SDK gives up | No reconnect; transcription dies into non-English garbage | SDK retries up to 4 times with exponential backoff; server drains stale state and accepts the reattach |
| Server-side rolling deploy kills an active session | Client sees `WS_CLOSE_SERVER_RESTART`, has to start over | Client sees a typed `reconnectable_close` reason, reconnects to a different worker, replays the buffer |
| Mobile SDK reconnects but audio has moved past the replay window | Reconnect silently succeeds but the ~2 s gap is transcribed as "..." | Server emits `replay_gap_detected` warning with the missing audio interval so the client can decide to fail loud or accept the gap |

---

## Motivation

M24 already built:

- Persistent session state in Postgres
- Audio buffering to S3 during the session
- `resume_session_id` query param on the WebSocket
- Transcript stored as it's produced

What M24 **didn't** build (tracked as follow-on, never scoped):

1. **A client-side replay buffer.** The Python SDK's `RealtimeClient` doesn't keep the last N seconds of audio on the client side, so even if it reconnects, there's no way to replay the words that didn't land.
2. **ACK protocol from server → client.** The server doesn't tell the client which audio chunks have been committed to the worker, so the client can't know what to retransmit vs. what to trust.
3. **Reattach-and-drain on the server.** The gateway accepts `resume_session_id` but doesn't drain the pending worker state — it just opens a fresh session with the same ID.
4. **Explicit gap reporting.** If there's a gap, the server silently transcribes around it rather than telling the client.

The 2026 `livekit/agents#4609` issue crystallized exactly this failure mode on ElevenLabs: the SDK sees a retryable error, doesn't actually retry, and the stream produces garbage. Dalston can ship a cleaner answer because the persistence foundation from M24 is already in place.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                  CLIENT-SIDE RING BUFFER                                 │
│                                                                          │
│  Mic ─▶ RingBuffer(cap=10 s) ──send──▶ WebSocket                        │
│            │                              │                             │
│            │         ◀────ack(seq=N)──────┘                             │
│            ▼                                                             │
│         drop all entries with seq ≤ N                                    │
│                                                                          │
│  On disconnect:                                                          │
│    - kept entries = seq > last_acked                                     │
│    - reconnect WS with resume_session_id=<id>&last_acked_seq=<N>         │
│    - replay kept entries in order                                        │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│                  SERVER REATTACH PATH                                    │
│                                                                          │
│  WS open (resume_session_id=S, last_acked_seq=N)                         │
│    │                                                                     │
│    ▼                                                                     │
│  load session S from Postgres                                            │
│    │                                                                     │
│    ▼                                                                     │
│  has_worker = session_router.find_worker(S)                              │
│    │                                                                     │
│    ├─ yes (worker still alive) ──▶ reattach, drain, resume                │
│    │                                                                     │
│    └─ no  ─▶ allocate new worker                                         │
│                │                                                         │
│                ▼                                                         │
│           seed worker state from S3 audio up to last committed turn       │
│                │                                                         │
│                ▼                                                         │
│           client replays seq > N ──▶ worker                              │
│                │                                                         │
│                ▼                                                         │
│           resume streaming                                               │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Steps

### 90.1: Audio chunk sequence numbers + server ACKs

**Files modified:**

- `dalston/realtime_sdk/protocol.py` — new `audio_ack` event; `audio_chunk` gains `seq: int`
- `dalston/gateway/api/v1/realtime.py` — track `last_committed_seq` per session, emit ACKs
- `dalston/gateway/api/v1/elevenlabs_stt.py` — same for ElevenLabs-compat route
- `tests/unit/test_realtime_ack.py` *(new)*

**Deliverables:**

Every inbound `audio_chunk` message carries a monotonically increasing `seq`. The server ACKs a chunk once it has been (a) decoded, (b) fed into the VAD endpointer, and (c) enqueued to the worker's input buffer. ACK carries `{seq: N, committed_at_audio_s: 12.34}`.

The ACK does **not** wait for transcription. That would be too slow on long utterances. "Enqueued to the worker" is the commit boundary — a value the client can reasonably trust to drop from its replay buffer.

```python
# dalston/realtime_sdk/protocol.py
class AudioChunk(BaseModel):
    type: Literal["audio_chunk"] = "audio_chunk"
    audio_base64: str
    seq: int

class AudioAck(BaseModel):
    type: Literal["audio_ack"] = "audio_ack"
    seq: int
    committed_at_audio_s: float
```

**ACK frequency tuning:** emitting an ACK for every 20 ms chunk is wasteful. The gateway batches ACKs: send one every 100 ms OR when a turn commits, whichever comes first. The batched ACK carries the highest committed `seq`.

**Backwards compatibility:** clients that don't send `seq` get `seq = None`, server doesn't ACK those chunks, the old flow continues to work as today. No existing client breaks.

---

### 90.2: Client-side ring buffer in `dalston_sdk.realtime`

**Files modified:**

- `sdk/dalston_sdk/realtime.py` — `_ChunkRingBuffer` class, sequence tracking
- `sdk/dalston_sdk/_reconnect.py` *(new)* — retry policy
- `tests/unit/test_sdk_realtime_buffer.py` *(new)*

**Deliverables:**

A fixed-capacity ring buffer in the Python SDK that holds sent-but-not-ACKed audio chunks. Default capacity: 10 s of audio (so ~500 chunks at 20 ms each). Behavior:

- On send: append `(seq, bytes)` to ring, evict oldest if full.
- On `audio_ack`: drop all entries with `seq <= acked_seq`.
- On disconnect: retain all entries still in the ring for replay.

```python
class _ChunkRingBuffer:
    """Fixed-capacity FIFO of un-ACKed audio chunks."""

    def __init__(self, max_seconds: float = 10.0, chunk_duration_ms: float = 20.0) -> None:
        self.capacity = int(max_seconds * 1000 / chunk_duration_ms)
        self._buf: collections.deque[tuple[int, bytes]] = collections.deque(maxlen=self.capacity)
        self._last_seq = -1

    def record(self, chunk: bytes) -> int:
        self._last_seq += 1
        self._buf.append((self._last_seq, chunk))
        return self._last_seq

    def ack(self, seq: int) -> None:
        while self._buf and self._buf[0][0] <= seq:
            self._buf.popleft()

    def pending(self) -> list[tuple[int, bytes]]:
        return list(self._buf)
```

**Capacity policy:** if the ring is full and chunks are being evicted before ACK, the SDK logs a `replay_buffer_full` warning. This signals either a too-small buffer or a server that's not ACKing — both are actionable.

---

### 90.3: Reconnect + replay in the SDK

**Files modified:**

- `sdk/dalston_sdk/realtime.py` — reconnect loop
- `sdk/dalston_sdk/_reconnect.py`

**Deliverables:**

When the WebSocket closes with a reconnectable reason code, the SDK:

1. Sleeps with exponential backoff: 200 ms, 400 ms, 800 ms, 1.6 s, give up after 4 attempts.
2. Opens a new WebSocket with `resume_session_id=<same>&last_acked_seq=<N>`.
3. On success, waits for the server to emit `session_resumed` (see 90.4).
4. Replays every pending chunk from the ring buffer in order, with the original `seq` preserved.
5. Resumes normal streaming.

**Which close codes are reconnectable:**

```python
RECONNECTABLE_CODES = {
    WS_CLOSE_ABNORMAL,         # 1006 — network drop
    WS_CLOSE_SERVICE_RESTART,  # 1012 — server rolling deploy
    WS_CLOSE_TRY_AGAIN_LATER,  # 1013 — server backpressure
    WS_CLOSE_DALSTON_RECONNECT,  # 4999 — new Dalston code, server-initiated reattach hint
}
```

Non-reconnectable codes (auth failure, bad request, lag exceeded from M53) propagate as before.

---

### 90.4: Server-side reattach handshake

**Files modified:**

- `dalston/gateway/api/v1/realtime.py` — handle `resume_session_id` + `last_acked_seq`
- `dalston/gateway/services/session_state.py` *(new or extend existing)*
- `dalston/session_router/worker_pool.py` — find-existing-worker path
- `tests/integration/test_realtime_reconnect.py` *(new)*

**Deliverables:**

The gateway already accepts `resume_session_id`. M90 turns it into an actual reattach:

1. **Load session state from Postgres.** Already done by M24.
2. **Check if the original worker is still alive.** If yes, reattach: kill any dangling client task, point the new WebSocket at the existing worker.
3. **If the worker is gone, allocate a new one and seed it.** Seeding replays S3-archived audio up to the **last committed turn** (not the last chunk — committed turns are the durable restart point).
4. **Emit `session_resumed` to the client** with `{last_committed_seq: N, gap_start_audio_s: T?, gap_end_audio_s: T?}`.
5. **Accept the client's replayed chunks.** Drop any that are `seq <= last_committed_seq` (already seen), process the rest normally.

```json
// New event sent immediately after a successful reattach
{
  "type": "session_resumed",
  "session_id": "sess_abc",
  "last_committed_seq": 142,
  "ready_at": "2026-04-15T12:34:56.789Z"
}
```

**Gap detection:** If `last_acked_seq` from the client is higher than any seq the server has in its state (i.e., ACKs got lost before the drop), that's fine — the client already trusts those chunks. If `last_acked_seq` is **lower** and there are un-seen seqs, that's expected. If the client's oldest pending seq is **newer** than the server's `last_committed_seq + 1`, there's a gap — emit `replay_gap_detected` with the missing interval and let the client decide whether to fail.

---

### 90.5: Close-code hygiene

**Files modified:**

- `dalston/gateway/api/v1/realtime.py` — replace generic `WS_CLOSE_ABNORMAL` with typed codes
- `dalston/realtime_sdk/protocol.py` — close code constants

**Deliverables:**

Right now several server-initiated failures close with `WS_CLOSE_ABNORMAL` (1006), which the client can't distinguish from a network drop. M90 introduces typed codes so the client knows whether to retry:

| Code | Name | Reconnectable |
| --- | --- | --- |
| 4001 | `WS_CLOSE_AUTH` | no |
| 4002 | `WS_CLOSE_BAD_REQUEST` | no |
| 4003 | `WS_CLOSE_LAG_EXCEEDED` (from M53) | no |
| 4004 | `WS_CLOSE_SESSION_EXPIRED` | no |
| 4999 | `WS_CLOSE_RECONNECT_HINT` | yes |
| 1012 | `WS_CLOSE_SERVICE_RESTART` (stdlib) | yes |

The server emits a JSON close reason `{type, reason, retryable: bool}` alongside the code for SDKs that surface it.

---

### 90.6: Integration test: simulate the `livekit/agents#4609` repro

**Files modified:**

- `tests/integration/test_realtime_reconnect.py`

**Deliverables:**

One end-to-end test that reproduces the 2026 LiveKit complaint and asserts Dalston's answer:

1. Start a realtime session, stream 5 s of audio, get a partial back.
2. Kill the WebSocket from the middle (simulate network drop).
3. Assert the SDK reconnects within 2 s.
4. Assert the server emits `session_resumed`.
5. Stream another 5 s of audio.
6. Assert the final transcript covers **all 10 s** of speech with no dropped words and no duplicate partials.

Plus:

- A unit test for the ring buffer's eviction under load.
- A gateway unit test for the reattach handshake when the original worker is still alive.
- A gateway unit test for the "worker gone, seed from S3" path.

---

## Non-Goals

- **Realtime session migration across machines** — If a session's original worker is gone, M90 starts a new worker on a possibly-different host. It does not implement live migration of worker state (that would need shared memory or state serialization). The audio replay path covers the same ground at a coarser granularity.
- **Reconnect on auth failure** — Typed close code 4001. Client must re-authenticate; no automatic retry.
- **Gap filling** — If there's a genuine gap because the client buffer overflowed, M90 reports it with `replay_gap_detected`. It does not attempt to synthesize or interpolate the missing audio.
- **Replay of finalized transcripts** — The client already has all final transcripts it received before the drop. M90 focuses on audio chunks the worker didn't yet commit. Transcripts delivered after the drop are up to the normal transcript streaming path.
- **Realtime multichannel reconnect** — M89's scope covers batch multichannel. Realtime multichannel + reconnect is a separate milestone if it's ever needed.

---

## Deployment

Rolling deploy. The new sequence + ACK protocol is additive — clients without `seq` still work on the new server, and the new SDK's buffer simply never receives ACKs from an old server (and so keeps all chunks pending until a hard timeout). Both degradation paths are safe.

The SDK bump (sequence numbers, ring buffer, reconnect loop) is a **minor** version (additive features, existing callers unchanged). Document the new `ReconnectPolicy` kwarg in the SDK changelog.

Session state in Postgres gains two new columns via an append-only migration:

```sql
ALTER TABLE realtime_sessions
    ADD COLUMN last_committed_seq BIGINT NULL,
    ADD COLUMN last_committed_at_audio_s DOUBLE PRECISION NULL;
```

---

## Verification

```bash
make dev

# 1. SDK reconnect test end-to-end
python -m pytest tests/integration/test_realtime_reconnect.py::test_drop_and_replay_full_audio -v

# 2. #4609 regression test
python -m pytest tests/integration/test_realtime_reconnect.py::test_livekit_4609_regression -v

# 3. Manual repro with a script
python scripts/test_realtime_reconnect.py \
  --audio tests/fixtures/audio/10s-speech.wav \
  --drop-at 4.5 \
  --expected-words "$(cat tests/fixtures/audio/10s-speech.reference.txt)"

# Expected: SDK logs reconnect, server logs session_resumed, final transcript WER < 3%

# 4. Server-initiated reconnect (rolling deploy simulation)
# Kill gateway container mid-session; compose restart policy brings it back.
# Assert the SDK reconnects and the transcript completes.

# 5. Close code hygiene
python -m pytest tests/unit/test_realtime_close_codes.py -v
```

---

## Checkpoint

- [ ] **90.1** `audio_chunk.seq` field on inbound protocol
- [ ] **90.1** Server emits batched `audio_ack` with committed `seq`
- [ ] **90.2** `_ChunkRingBuffer` with 10 s default capacity
- [ ] **90.2** SDK evicts on ACK, retains on disconnect
- [ ] **90.3** SDK exponential-backoff reconnect loop with cap (200 ms → 1.6 s, 4 retries)
- [ ] **90.3** SDK replays pending chunks after `session_resumed`
- [ ] **90.4** Gateway reattaches to live worker when present
- [ ] **90.4** Gateway seeds new worker from S3 when original is gone
- [ ] **90.4** `session_resumed` + `replay_gap_detected` events
- [ ] **90.5** Typed close codes for all server-initiated close paths
- [ ] **90.6** `livekit/agents#4609` regression test green
- [ ] **90.6** 10-second drop-and-resume E2E test green with WER < 3%
- [ ] Postgres migration for `last_committed_seq` columns
- [ ] No regression in existing realtime integration suite
