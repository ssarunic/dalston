# M88: Telephony Voice-Agent Readiness

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | Make Dalston the drop-in realtime STT for Twilio/LiveKit/Pipecat voice agents: decode telephony codecs, hit sub-300 ms end-of-turn commit, forward VAD tuning end-to-end |
| **Duration**       | 6–9 days                                                     |
| **Dependencies**   | M62 G-9 (`commit_strategy` default), G-11 (ulaw_8000 decode), G-12 (VAD tuning forward) — this milestone assumes those land first, then goes beyond parity |
| **Deliverable**    | μ-law + A-law codec helper, `commit_strategy=vad_aggressive`, `turn.ended` event, forwarded VAD tuning params, narrowband upsampling, LiveKit/Pipecat integration tests |
| **Status**         | Not Started                                                  |

## User Story

> *"As a voice-agent developer migrating from ElevenLabs Scribe, I want to point my Twilio SIP stream at Dalston's `/v1/speech-to-text/realtime` and get final transcripts within 300 ms of the caller stopping speaking — without my agent stalling for three seconds waiting for a `committed_transcript` message, and without my μ-law audio being interpreted as PCM garbage."*

---

## Outcomes

| Scenario | Current | After M88 |
| -------- | ------- | --------- |
| Twilio Media Streams (μ-law 8 kHz) hits `/v1/speech-to-text/realtime?audio_format=ulaw_8000` | Gateway base64-decodes then treats every byte as PCM16 → transcription is garbage | μ-law decoded to PCM16, upsampled to 16 kHz, forwarded to worker as normal PCM |
| LiveKit agent using Scribe-compat endpoint waits on final transcript after silence | Final transcript fires only after the next partial batch — observed 1–3 s tail latency | `turn.ended` + final transcript within 200–300 ms of silence with `commit_strategy=vad_aggressive` |
| Caller's audio has 400 ms silence in the middle of a thought | VAD cuts the turn mid-sentence | `min_silence_duration_ms` forwarded from client request; caller can tune per session |
| LiveKit `livekit/agents#4255` repro case | Zero transcriptions returned | Pass matching `deepgram-nova-3` behavior — audio decoded, VAD runs, partials and finals emit |
| Narrowband PSTN audio (8 kHz μ-law) on a 16 kHz-only engine | Engine rejects or downgrades | Gateway upsamples to 16 kHz via sinc resampler before forwarding |

---

## Motivation

2026 voice-agent developers are loud about three things on `livekit/agents` and in comparison blogs:

1. **`livekit/agents#4087`** — Scribe v2's `committed_transcript` arrives "many seconds after the user stopped talking". Marketing says 150 ms; real end-of-turn is multi-second.
2. **`livekit/agents#4255`** — Scribe v2 Realtime produces zero transcriptions via LiveKit on audio where Deepgram Nova-3 works perfectly.
3. **`livekit/agents#4810`** — Users asking ElevenLabs to expose their faster commit strategy and VAD signals.

M62 already has G-9 (flip `commit_strategy` default from `vad` to `manual` for parity), G-11 (decode `ulaw_8000` instead of corrupting it), and G-12 (forward VAD tuning params end-to-end). Those close the parity gap. They do **not** win the voice-agent use case, because "parity with a slow vendor" is still slow.

M88 takes the next step: after M62's fixes land, expose an **aggressive** VAD commit mode with a tight endpointing window, emit a dedicated `turn.ended` event the moment VAD sees sustained silence, and make sure the μ-law path is not just "not corrupted" but **actually fast** (no buffering, no extra copy). The competitive wedge is "swap Scribe for Dalston, your agent feels snappier the same day".

---

## Architecture

```
┌───────────────────────────────────────────────────────────────────────┐
│                 REALTIME PATH (gateway → worker)                      │
│                                                                       │
│   WS client ──audio_chunks──▶ gateway                                 │
│                               │                                       │
│                               ▼                                       │
│                     ┌────────────────────┐                            │
│                     │  codec_pipeline    │  ulaw_8000 / alaw_8000     │
│                     │  decode → PCM16    │  pcm_8000 / pcm_16000      │
│                     │  resample 8k → 16k │                            │
│                     └────────┬───────────┘                            │
│                              │ PCM16 @ 16 kHz                         │
│                              ▼                                        │
│                     ┌────────────────────┐                            │
│                     │  VAD endpointer    │  silence ≥ min_silence_ms  │
│                     │  (Silero ONNX)     │  → emit turn.ended ──┐     │
│                     └────────┬───────────┘                      │     │
│                              │                                  │     │
│                              ▼                                  │     │
│                      worker transcribe                          │     │
│                              │                                  │     │
│                              ▼                                  │     │
│                     partial / final / turn.ended ◀──────────────┘     │
│                              │                                        │
│                              ▼                                        │
│                          WS client                                    │
└───────────────────────────────────────────────────────────────────────┘
```

---

## Steps

### 88.1: `codec_pipeline` helper for realtime audio

**Files modified:**

- `dalston/gateway/realtime/codec.py` *(new)* — shared codec pipeline
- `dalston/gateway/api/v1/realtime.py` — call codec pipeline before worker forward
- `dalston/gateway/api/v1/openai_realtime.py` — same
- `tests/unit/test_codec_pipeline.py` *(new)*

**Deliverables:**

One helper that all three realtime endpoints (Dalston native, ElevenLabs, OpenAI) share. Builds on whatever M62 G-11 lands but consolidates it so neither endpoint reimplements decoding.

```python
# dalston/gateway/realtime/codec.py

@dataclass(frozen=True)
class CodecSpec:
    """Declared audio format from the client."""
    encoding: Literal["pcm_s16le", "pcm_f32le", "ulaw", "alaw"]
    sample_rate: int  # 8000 or 16000
    channels: int = 1

class CodecPipeline:
    """Decode client audio to PCM16 @ target sample rate for worker ingest.

    Stateless per-chunk: one decode path for ulaw/alaw, one passthrough for
    pcm_s16le, one cast for pcm_f32le. Resampling uses a polyphase sinc
    filter (scipy.signal.resample_poly) with cached taps per (src, tgt).
    """

    def __init__(self, source: CodecSpec, target_sample_rate: int = 16000) -> None: ...

    def decode_chunk(self, raw: bytes) -> bytes:
        """raw is base64-decoded bytes from the WS frame. Returns PCM16 @ target_sr."""
        ...

    @classmethod
    def from_audio_format(cls, audio_format: str, target_sr: int = 16000) -> CodecPipeline:
        """Parse ElevenLabs-style audio_format strings like 'ulaw_8000', 'pcm_16000'."""
        ...
```

**μ-law / A-law decode:** `audioop.ulaw2lin(raw, 2)` / `audioop.alaw2lin(raw, 2)` from the stdlib. Python 3.13 removed `audioop`; use the `audioop-lts` backport declared in `pyproject.toml` under `[project.optional-dependencies].gateway`.

**Resampling:** 8 kHz → 16 kHz via `scipy.signal.resample_poly(pcm, 2, 1)`. Cache the filter taps on the `CodecPipeline` instance — filter design is ~5 ms, one-time per session.

**Per-chunk latency target:** <1 ms for a 20 ms μ-law chunk on a modern CPU. This sits on the hot path for every audio frame so unit tests include a benchmark assertion.

**Tests:**

- `test_ulaw_decode_matches_reference` — run a known μ-law file through the pipeline, diff against the PCM16 reference from `ffmpeg -ar 16000`. Tolerate 1-LSB rounding.
- `test_alaw_decode_matches_reference`
- `test_pcm_passthrough_zero_copy` — PCM16 @ 16 kHz must not be copied if codec matches.
- `test_upsample_preserves_signal_energy` — 8 → 16 kHz RMS within 0.5 dB of input.
- `test_decode_chunk_under_1ms` — 20 ms chunk, warm pipeline, wall-clock budget.

---

### 88.2: `commit_strategy=vad_aggressive` mode

**Files modified:**

- `dalston/gateway/api/v1/realtime.py` — extend `commit_strategy` validator to accept `vad_aggressive`
- `dalston/gateway/api/v1/elevenlabs_stt.py` — same for ElevenLabs-compat route
- `dalston/realtime_sdk/vad.py` — expose `aggressive=True` VAD config preset
- `dalston/common/audio_defaults.py` — defaults table

**Deliverables:**

A third `commit_strategy` value, on top of the `manual` / `vad` pair M62 lands. Aggressive mode:

- `min_silence_duration_ms = 150` (default `vad` mode uses 400 ms)
- `speech_threshold = 0.4` (more sensitive than default 0.5)
- Triggers turn commit on **first** VAD silence event, not averaged over `lookback_chunks`
- Client request can override any of these via `vad_min_silence_ms`, `vad_speech_threshold`, `vad_lookback_chunks` query params (those are the G-12 parity fields; this step plugs them into the hot path)

```python
# dalston/common/audio_defaults.py

COMMIT_STRATEGY_PRESETS: dict[str, VADConfig] = {
    "vad": VADConfig(
        min_silence_duration=0.4,
        speech_threshold=0.5,
        lookback_chunks=3,
    ),
    "vad_aggressive": VADConfig(
        min_silence_duration=0.15,
        speech_threshold=0.4,
        lookback_chunks=2,
    ),
}
```

**Tradeoff acknowledgement:** `vad_aggressive` **will** cut turns mid-thought on hesitant speakers. It is opt-in, documented as "for phone-style back-and-forth where latency matters more than never cutting off". The parity default stays `manual`.

**Tests:**

- `test_vad_aggressive_commits_in_150ms_of_silence` — synthetic audio with exactly 150 ms silence tail, assert `turn.ended` fires within 200 ms wall-clock.
- `test_vad_forwards_client_override` — client sends `vad_min_silence_ms=80`, assert VAD instance uses 80 ms.
- `test_vad_standard_still_400ms` — regression.

---

### 88.3: `turn.ended` WebSocket event

**Files modified:**

- `dalston/realtime_sdk/protocol.py` — new event type
- `dalston/gateway/api/v1/realtime.py` — emit when VAD endpoints
- `docs/specs/realtime/WEBSOCKET_API.md` — document event

**Deliverables:**

A dedicated event that fires **the instant VAD declares end-of-turn**, independent of whether the final transcript has come back yet. This is what voice-agent frameworks want so they can start LLM inference on the partial transcript while the final is still being formatted.

```json
// New event
{
  "type": "turn.ended",
  "session_id": "sess_abc",
  "turn_id": "turn_1",
  "committed_at_audio_s": 12.340,
  "commit_reason": "vad_silence",
  "trailing_silence_ms": 152
}
```

Followed (within a few hundred ms) by the existing `transcript.final` / ElevenLabs `committed_transcript` carrying the actual text.

**Contract notes:**

- The event is additive. Clients that don't care can ignore it. No existing client breaks.
- It is emitted **before** the final transcript, never after.
- `commit_reason` is one of `vad_silence`, `manual_commit`, `max_turn_duration`.
- On the ElevenLabs-compat route, also emit the standard `committed_transcript` when text is ready — `turn.ended` is a Dalston extension that ElevenLabs callers can opt into via a query param `dalston_events=true`. Without that param the ElevenLabs route stays bit-for-bit compatible.

---

### 88.4: Narrowband upsampling for 16 kHz-only engines

**Files modified:**

- `dalston/gateway/realtime/codec.py` — target sample rate is per-engine, not global
- `dalston/session_router/worker_pool.py` — worker advertises `accepted_sample_rates`

**Deliverables:**

When a client sends `ulaw_8000` and the routed worker only accepts 16 kHz, the codec pipeline upsamples. When the worker accepts 8 kHz natively (some telephony-tuned models do), forward at 8 kHz and save compute.

```python
# Worker capability declared in engine.yaml
capabilities:
  realtime:
    accepted_sample_rates: [8000, 16000]   # or [16000] only
```

Pipeline reads the worker's advertised rates and picks the highest that matches:

```python
pipeline = CodecPipeline.from_audio_format(
    audio_format, target_sr=worker.pick_preferred_rate(source_sr=8000)
)
```

**Tests:**

- `test_upsample_when_worker_only_16k`
- `test_passthrough_when_worker_accepts_8k`

---

### 88.5: LiveKit + Pipecat integration smoke tests

**Files modified:**

- `tests/integration/test_livekit_compat.py` *(new)*
- `tests/integration/test_pipecat_compat.py` *(new)*
- `docs/guides/voice-agents.md` *(new)* — setup guide

**Deliverables:**

Two integration tests that pin Dalston's behavior against the repros from the 2026 GitHub issue tracker:

- **`test_livekit_4255_regression`** — exact audio payload that produced zero transcriptions on Scribe v2, asserts Dalston emits at least one final transcript with non-zero duration.
- **`test_livekit_4087_end_of_turn_latency`** — synthetic audio with a clear pause, asserts `turn.ended` fires within 300 ms of the silence start in `vad_aggressive` mode.
- **`test_pipecat_smoke`** — boot a Pipecat pipeline pointed at Dalston, run 30 s of speech through, assert no dropped frames.

Plus a voice-agent setup guide that walks through the Twilio → LiveKit → Dalston path with a working `docker-compose.voice-agent.yml` overlay.

---

## Non-Goals

- **Custom worker-side VAD for CPU engines** — The realtime SDK already uses Silero ONNX on the worker. M88 tunes thresholds and commit timing; it does not replace the VAD implementation.
- **Sub-150 ms end-of-turn** — That requires architectural changes (streaming partials on every ~50 ms chunk, worker-side semantic endpointing). Tracked separately as a follow-up.
- **PCM16 @ 24 kHz support** — OpenAI-compat route uses 24 kHz; that's already supported by the existing resampler path. Scope here is 8 kHz telephony.
- **TTS echo cancellation** — Agent frameworks handle this upstream. Out of scope.
- **M62 parity gaps G-9, G-11, G-12** — Those are M62's job. M88 depends on them landing first and then builds on top. If G-11 slips, M88 steps 88.1 can absorb it, but the preferred ordering is M62 → M88.

---

## Deployment

Rolling deploy. Clients that don't opt into `vad_aggressive` or `turn.ended` events see identical behavior to pre-M88.

**Codec dependency:** `audioop-lts>=0.2.1` added to the gateway's optional extras. `scipy>=1.11` is already a transitive dependency of the engines; explicitly declare it in the gateway's extras as well.

**Worker capability advertisement:** Engines built before M88 won't declare `accepted_sample_rates`. Default fallback is `[16000]`, matching current behavior.

---

## Verification

```bash
make dev

# 1. μ-law round-trip: Twilio-shaped payload transcribed correctly
python scripts/test_elevenlabs_realtime.py \
  --audio tests/fixtures/audio/twilio-ulaw-8k-sample.ulaw \
  --audio-format ulaw_8000 \
  --commit-strategy vad_aggressive \
  --dalston-events true

# Expected: at least one transcript.final within 2 s of audio end,
# turn.ended event within 300 ms of trailing silence start.

# 2. vad_aggressive end-of-turn timing
python -m pytest tests/integration/test_livekit_compat.py::test_livekit_4087_end_of_turn_latency -v

# 3. Codec pipeline micro-benchmark
python -m pytest tests/unit/test_codec_pipeline.py::test_decode_chunk_under_1ms -v

# 4. Zero-transcription regression
python -m pytest tests/integration/test_livekit_compat.py::test_livekit_4255_regression -v
```

---

## Checkpoint

- [ ] **88.1** `CodecPipeline` helper decodes ulaw/alaw → PCM16 and resamples 8 k → 16 k under 1 ms per 20 ms chunk
- [ ] **88.1** All three realtime endpoints route through the single helper (no duplicate decoding)
- [ ] **88.2** `commit_strategy=vad_aggressive` accepted on realtime routes
- [ ] **88.2** Client-supplied VAD tuning overrides (`vad_min_silence_ms` etc.) forwarded end-to-end
- [ ] **88.3** `turn.ended` event emitted on VAD endpoint with `commit_reason` and `trailing_silence_ms`
- [ ] **88.3** ElevenLabs-compat route stays bit-compatible unless `dalston_events=true` is set
- [ ] **88.4** Workers advertise `accepted_sample_rates`; pipeline picks best rate
- [ ] **88.5** LiveKit `#4087` and `#4255` regression tests green
- [ ] **88.5** Voice-agent setup guide merged with working compose overlay
- [ ] Existing `commit_strategy=manual` / `vad` paths unaffected (regression suite green)
