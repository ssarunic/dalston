# OpenAI ASR API — Parity Gap Analysis

**Date**: 2026-03-08
**Source spec**: OpenAI OpenAPI spec (Stainless, March 2026)
**Scope**: `POST /v1/audio/transcriptions`, `POST /v1/audio/translations`, `WS /v1/realtime`

---

## How to Read This Document

Each gap is assessed on three dimensions:

| Feasibility | Meaning |
|-------------|---------|
| ✅ Current | Closable with gateway/adapter code alone — no engine or architecture changes needed |
| 🔧 New capability | Requires changes to real-time workers, batch engines, or pipeline schema |
| ❌ Not feasible | Structurally blocked by architecture, a hard dependency, or a constraint we have deliberately accepted |

Remedies are grouped into three tiers by implementation cost:

- **Easy** — single-file, < 1 day, no risk to stability
- **Medium** — cross-cutting change (multiple files or services), 1–5 days, needs test coverage
- **Hard** — new subsystem, engine change, or protocol extension, > 1 week, carries integration risk

---

## Part 1: POST /v1/audio/transcriptions

### G-1 · `stream=true` SSE streaming not implemented

**Gap**: When `stream=true`, OpenAI returns `text/event-stream` with incremental
`transcript.text.delta` and `transcript.text.done` events as transcription progresses.
Dalston in OpenAI mode always waits for full completion, then returns a single JSON body.

**Feasibility**: 🔧 New capability

The batch pipeline is inherently stage-sequential: audio goes through PREPARE → TRANSCRIBE
→ ALIGN → DIARIZE → MERGE before a result is available. Raw partial segments emerge from
the TRANSCRIBE stage, but they are not currently surfaced to the gateway.

To implement SSE:

1. Engines need to publish partial segment events to a Redis pub/sub channel keyed by `job_id`.
2. The gateway opens an SSE response before the job completes and consumes that channel.
3. A new `transcript.text.delta` SSE event type must be defined in the engine SDK.

The ALIGN and DIARIZE stages refine the transcript after the fact; partial streaming would
need to decide whether to stream raw TRANSCRIBE output (lower quality) or wait for
post-processed segments (delayed). OpenAI's model appears to stream raw ASR output.

**Remedy**: Hard. Requires a new engine-level streaming protocol, Redis pub/sub event
definitions, a gateway SSE handler, and a decision on quality vs. latency trade-off for
intermediate results.

---

### G-2 · `include=["logprobs"]` not implemented

**Gap**: OpenAI accepts `include=["logprobs"]` to attach log-probability data to each word
and segment in the verbose response. Dalston ignores this parameter entirely and never
returns `logprobs`.

**Feasibility**: 🔧 New capability

`faster-whisper` produces `avg_logprob` and per-token probabilities per segment.
These values are not currently captured in `pipeline_types.py` and are discarded by the
transcription engine before output is written to S3. The path to closing this gap:

1. Add `logprobs` fields to `TranscriptionOutput` in `pipeline_types.py`.
2. Update the faster-whisper (and whisperx) engines to capture and emit them.
3. Pass them through the merge engine into `transcript.json`.
4. Format them in `format_openai_response()` when the `include` parameter contains `"logprobs"`.

**Remedy**: Medium. No architectural change required, but touches engine output schema,
pipeline types, the merge engine, and the gateway formatter.

---

### G-3 · `chunking_strategy` parameter not implemented

**Gap**: OpenAI accepts `chunking_strategy: "auto"` to control how long audio files are
segmented before transcription. Dalston does not accept or forward this parameter.

**Feasibility**: ✅ Current (for accepting `"auto"`) / 🔧 New capability (for custom strategies)

`"auto"` is the only value OpenAI currently documents, and it corresponds to our existing
default VAD-based chunking behaviour. We can accept and silently validate the parameter
without any engine change, achieving spec compliance for today's OpenAI clients.

If OpenAI later adds explicit chunking configurations (e.g. fixed-interval or silence-based
thresholds), those would require PREPARE stage changes.

**Remedy**: Easy (for `"auto"` acceptance) — add the parameter to the form signature,
validate it against `{"auto"}`, and ignore it. Medium if actual custom chunking strategies
are needed.

---

### G-4 · `known_speaker_names` not implemented

**Gap**: OpenAI accepts an array of speaker name strings as hints (e.g. `["Alice", "Bob"]`)
to substitute for generic labels like `SPEAKER_0` in diarized output. Dalston ignores this.

**Feasibility**: ✅ Current

We already produce `speaker_id` labels in diarized transcripts. The final-merger engine
assembles speaker labels from the diarization stage. Adding a relabeling pass at merge time
that maps ordered speaker labels to provided names is a self-contained change.

**Remedy**: Medium. Requires: (1) gateway accepts and forwards `known_speaker_names` as a
job parameter, (2) DAG builder passes it to the merge engine, (3) merge engine applies the
label substitution map before writing `transcript.json`.

---

### G-5 · `known_speaker_references` not implemented

**Gap**: OpenAI accepts audio data URIs as reference clips for voice-print-based speaker
identification — i.e. "this clip is Alice, find her across the recording." Dalston has no
equivalent.

**Feasibility**: 🔧 New capability

This requires speaker embedding extraction (typically a separate neural model such as
pyannote's embedding extractor or resemblyzer) and a matching step during or after
diarization. The reference clips must be embedded, then compared against speaker clusters
produced by the diarize engine. `pyannote-audio` supports this via its speaker
verification pipeline, but we don't currently expose it.

**Remedy**: Hard. Requires a new pipeline stage (or extension of the diarize engine) for
speaker enrollment, a new job parameter type for audio data URIs, and changes to the
diarization engine to accept reference embeddings.

---

### G-6 · `diarized_json` format documented but silently falls through to `json`

**Gap**: `diarized_json` appears in the `Form` description at
[transcription.py:179](../../dalston/gateway/api/v1/transcription.py#L179) and in the OpenAI
spec, but it is **absent from `OPENAI_RESPONSE_FORMATS`** and unhandled in
`format_openai_response()`. Clients requesting `diarized_json` silently receive plain `json`
with no error. This is a correctness bug, not a feature gap.

**OpenAI's `diarized_json` schema**:

```json
{
  "speakers": [
    {
      "id": "speaker_0",
      "name": null,
      "segments": [
        { "start": 0.0, "end": 3.5, "text": "Hello world." }
      ]
    }
  ],
  "usage": { "type": "audio", "audio_seconds": 12.5 }
}
```

We already produce speaker-attributed segments in our diarized transcript output.

**Feasibility**: ✅ Current

**Remedy**: Easy. Two changes required:

1. Add `DIARIZED_JSON = "diarized_json"` to `OpenAIResponseFormat` in
   [openai_audio.py](../../dalston/gateway/api/v1/openai_audio.py).
2. Add a `diarized_json` branch in `format_openai_response()` that maps speaker segments
   from the transcript to OpenAI's schema.

---

### G-7 · `usage` field absent from all response schemas

**Gap**: All OpenAI response types (`json`, `verbose_json`, `diarized_json`) include a
`usage` object:

```json
{ "usage": { "type": "audio", "audio_seconds": 42.1 } }
```

Dalston returns none of this. The audio duration is available from the transcript's
`metadata.duration` field and from the job record.

**Feasibility**: ✅ Current

**Remedy**: Easy. Add `usage: dict` to `OpenAITranscriptionResponse` and
`OpenAIVerboseResponse`, and populate it from `transcript["metadata"]["duration"]` in
`format_openai_response()`.

---

### G-8 · `temperature=0` silently not forwarded to engine

**Gap**: In both transcription ([transcription.py:343](../../dalston/gateway/api/v1/transcription.py#L343))
and translation ([openai_translation.py:156](../../dalston/gateway/api/v1/openai_translation.py#L156)),
temperature is only forwarded when `> 0`:

```python
if temperature is not None and temperature > 0:
    parameters["temperature"] = temperature
```

A client explicitly sending `temperature=0` to disable sampling receives the engine's
default sampling behaviour instead. This is semantically wrong — `0` is not equivalent to
"omitted".

**Feasibility**: ✅ Current

**Remedy**: Easy. Change the condition to `if temperature is not None:`.

---

## Part 2: POST /v1/audio/translations

### G-9 · `stream=true` SSE streaming not implemented

Same root cause as G-1. Translation is a special case of transcription with forced English
output — SSE streaming applies identically.

**Feasibility**: 🔧 New capability
**Remedy**: Hard (same as G-1; translation endpoint would be addressed as part of the same work).

---

### G-10 · Translation accepts `text`, `srt`, `vtt` response formats beyond OpenAI spec

**Gap**: Dalston's translation endpoint accepts `text`, `srt`, and `vtt` in
`response_format`. The current OpenAI spec for `POST /audio/translations` only documents
`json` and `verbose_json`.

**Assessment**: This is a **harmless extension**, not a bug. Spec-compliant OpenAI clients
will never send these values. Dalston-native clients who happen to use an OpenAI model
with `response_format=srt` will get useful output. No action required — but this should
be documented as a deliberate extension.

---

## Part 3: WS /v1/realtime

### G-11 · `pcm16` treated as 16 kHz instead of OpenAI's 24 kHz

**Gap**: The OpenAI Realtime spec defines `pcm16` as 16-bit PCM at **24 kHz**.
Dalston maps it to 16 kHz (documented in the comment at
[openai_realtime.py:100](../../dalston/gateway/api/v1/openai_realtime.py#L100)):

```python
# Note: OpenAI spec uses 24kHz but our Silero VAD only supports 8kHz/16kHz
"pcm16": ("pcm_s16le", DEFAULT_SAMPLE_RATE),  # 16kHz
```

A spec-compliant client that sends 24 kHz PCM16 will have its audio interpreted at 16 kHz,
playing back at 0.67× speed with lower pitch. The engine will transcribe incorrect timing
and possibly garbled phonemes. This is the most impactful correctness bug in the realtime
path.

**Feasibility**: 🔧 New capability

Silero VAD itself only supports 8 kHz and 16 kHz. Two approaches:

- **Option A** (preferred): Add a resampling step in the realtime worker that downsamples
  incoming 24 kHz audio to 16 kHz before VAD. This is a standard DSP operation
  (scipy/numpy resample). The ASR engine then sees 16 kHz, which Whisper handles natively.
  Audio quality loss is minimal.
- **Option B**: Replace Silero VAD with one that supports 24 kHz (e.g. WebRTC VAD, or
  Silero v5 if it ever adds 24 kHz support). More invasive.

**Remedy**: Medium. Option A requires a resampling pass in the realtime worker engine, a
worker URL protocol update to pass the client's claimed sample rate, and corresponding
changes to `_proxy_to_worker_openai`.

---

### G-12 · `conversation.item.created` event not emitted

**Gap**: After `input_audio_buffer.committed`, OpenAI emits a `conversation.item.created`
event:

```json
{
  "type": "conversation.item.created",
  "event_id": "evt_...",
  "item": {
    "id": "item_...",
    "type": "message",
    "role": "user",
    "content": [{ "type": "input_audio", "audio": null, "transcript": null }]
  }
}
```

Dalston emits only `input_audio_buffer.committed`. Clients that track conversation item
state (as OpenAI's own SDKs do) will have a mismatch.

**Feasibility**: ✅ Current

**Remedy**: Easy. Emit `conversation.item.created` in the `input_audio_buffer.commit`
branch of `_openai_client_to_worker()`, just before the `committed` ack, using the same
`item_id`.

---

### G-13 · `input_audio_buffer.committed` missing `previous_item_id`

**Gap**: OpenAI's `committed` event includes both `item_id` (the newly committed item) and
`previous_item_id` (the preceding item, for sequencing). Dalston only sends `item_id`.

**Feasibility**: ✅ Current

**Remedy**: Easy. Add `previous_item_id` to `OpenAISessionState` and populate it before
rotating `current_item_id` on each commit.

---

### G-14 · VAD events missing `item_id`

**Gap**: OpenAI's `input_audio_buffer.speech_started` and `speech_stopped` events include
`item_id` to correlate speech detection with the subsequent transcript item. Dalston omits
it.

**Feasibility**: ✅ Current

**Remedy**: Easy. In `_openai_worker_to_client()`, include
`"item_id": session_state.current_item_id` in the `speech_started` and `speech_stopped`
translated events.

---

### G-15 · `turn_detection` configuration silently ignored

**Gap**: OpenAI's `transcription_session.update` accepts rich VAD parameters:

```json
{
  "turn_detection": {
    "type": "server_vad",
    "threshold": 0.5,
    "silence_duration_ms": 600,
    "prefix_padding_ms": 300
  }
}
```

Dalston treats `turn_detection` as a binary on/off flag and discards `threshold`,
`silence_duration_ms`, and `prefix_padding_ms`. These map directly to Silero VAD
parameters.

**Feasibility**: 🔧 New capability

The realtime worker session URL already accepts params like `enable_vad`. Extending the
protocol to pass through VAD threshold and silence duration is straightforward.

**Remedy**: Medium. (1) Extend `_build_worker_params()` to include VAD config fields.
(2) Update the realtime worker's VAD initialisation to consume them. (3) Update
`_handle_session_update()` to extract and store the values from `turn_detection`.

---

### G-16 · `transcription_session.created` returns minimal session object

**Gap**: Dalston sends a sparse session object on connection:

```json
{
  "type": "transcription_session.created",
  "session": {
    "id": "sess_...",
    "model": "gpt-4o-transcribe",
    "input_audio_format": "pcm16",
    "input_audio_transcription": { "model": "gpt-4o-transcribe" }
  }
}
```

OpenAI's full object also includes `turn_detection` (with defaults), `noise_reduction`,
and `client_secret`. SDKs that inspect session state on creation will see missing fields.

**Feasibility**: ✅ Current (for the structural fields; `noise_reduction` requires G-17)

**Remedy**: Easy. Extend the `transcription_session.created` send in
`openai_realtime_transcription()` to include default values for `turn_detection`
and `noise_reduction: null`.

---

### G-17 · `noise_reduction` session parameter not handled

**Gap**: OpenAI's session update accepts a `noise_reduction` object:

```json
{ "noise_reduction": { "type": "near_field" } }
```

Dalston ignores this field entirely. Most realtime whisper workers don't have a built-in
noise reduction pre-filter.

**Feasibility**: 🔧 New capability

Audio pre-processing (e.g. RNNoise, demucs-vocal-isolation) would need to be integrated
into the realtime worker pipeline as a pre-VAD step. This is a significant engine addition.

**Remedy**: Hard. Requires a new pre-processing stage in realtime workers and a worker
protocol extension. Could be deferred unless clients explicitly depend on it. For now,
the session updated response should acknowledge the field with `"noise_reduction": null`
to signal unsupported.

---

### G-18 · Verbose JSON stubs are inaccurate (per-segment quality signals)

**Gap**: Dalston hardcodes quality signals in every segment of a `verbose_json` response:

```python
tokens=[],
avg_logprob=-0.5,     # fixed sentinel
compression_ratio=1.0, # fixed sentinel
no_speech_prob=0.02,   # fixed sentinel
```

`faster-whisper` and `whisperx` both emit real values for these per segment. Clients
using `avg_logprob` or `no_speech_prob` for quality filtering will get misleading data.

**Feasibility**: 🔧 New capability

The values are discarded by the transcription engine before output is written to S3.
They need to be: (1) added to `TranscriptionSegment` in `pipeline_types.py`, (2) captured
and emitted by the faster-whisper and whisperx engines, (3) passed through the merge engine
into `transcript.json`, (4) read and formatted in `format_openai_response()`.

**Remedy**: Medium. Same pipeline path as G-2; can be done in the same pass.

---

## Part 4: Blind Spots

These are issues not covered by the gap list above that could affect real-world clients.

---

### B-1 · `prompt` passed as a raw string to an engine expecting a term array

In OpenAI mode, the `prompt` parameter (free prose text, max 224 tokens) is stored
directly as `parameters["vocabulary"] = prompt` in both transcription and translation.
However, the Dalston pipeline's `vocabulary` parameter is intended to be a **JSON array
of boost terms** — and the engines that receive it may expect a list, not a prose string.

In the realtime path the same parameter is split on commas: `prompt.split(",")`. The batch
path passes the raw string. The two paths treat the same conceptual parameter differently,
and neither correctly implements OpenAI's intent (which is to feed the model a priming
context, not a term list).

**Risk**: Vocabulary boosting silently does nothing, or causes engine-side parse errors.
**Remedy**: Easy-Medium. Decide on a canonical mapping (e.g. split on whitespace/commas
and deduplicate for the engine's term-boost path) and apply it consistently across both
paths.

---

### B-2 · Rate-limit response headers not returned

OpenAI clients and SDKs inspect `x-ratelimit-limit-requests`, `x-ratelimit-remaining-requests`,
and `x-ratelimit-reset-requests` headers to implement adaptive backoff. Dalston returns HTTP
429 but does not include these headers. Libraries that use the headers for retry scheduling
(e.g. `openai-python`) will fall back to exponential backoff rather than using the exact
reset time.

**Feasibility**: ✅ Current
**Remedy**: Easy. Add a response middleware that injects rate-limit headers from the
`RedisRateLimiter.check_*` result into HTTP responses.

---

### B-3 · `model` not echoed in transcription responses

OpenAI's responses include a `model` field indicating which model actually processed the
request. Dalston's `json` and `verbose_json` responses contain no model field. Clients
that log or audit which model handled a request will be unable to determine this.

**Feasibility**: ✅ Current
**Remedy**: Easy. Add `model: str` to `OpenAITranscriptionResponse` and
`OpenAIVerboseResponse` and populate it from the resolved engine identifier (available
from the job record after completion).

---

### B-4 · `prompt` token-length validation not enforced

The `Form` description documents "max 224 tokens" for `prompt`, matching OpenAI's limit,
but the gateway never validates this. A client sending a very long prompt could cause
unexpected engine behaviour (truncation, performance degradation, or silent quality loss)
with no error returned.

**Feasibility**: ✅ Current
**Remedy**: Easy. Add a tokenisation check (use `tiktoken` or a rough character-count
heuristic — 224 tokens ≈ 900 characters) in `validate_openai_request()`.

---

### B-5 · Large URL-based audio downloads bypass the 25 MB limit effectively

The 25 MB file size check runs on `ingested.content` after the full download:

```python
if len(ingested.content) > OPENAI_MAX_FILE_SIZE:
    raise_openai_error(...)
```

For `audio_url`-based ingestion, the gateway downloads the entire file into memory before
checking the size. A 500 MB file passed via URL in OpenAI model mode will exhaust worker
memory before the limit is enforced.

**Feasibility**: ✅ Current
**Remedy**: Easy-Medium. The ingestion service should stream-download and abort early if
content-length exceeds the limit, or validate the `Content-Length` header before
downloading.

---

### B-6 · Binary realtime audio frames bypass format validation

Raw binary WebSocket frames sent to `/v1/realtime` are passed directly to the worker
without verifying they match the negotiated `input_audio_format`. If a client sends G.711
ulaw bytes but the session was configured for `pcm16`, the realtime worker will silently
process garbage, producing nonsensical transcription with no error.

**Feasibility**: ✅ Current (for header validation) / 🔧 New capability (for deep validation)
**Remedy**: Easy for structural checks (e.g. detect obvious non-PCM framing); Medium for
full per-frame format verification.

---

### B-7 · Synchronous OpenAI mode does not scale under concurrent long-running jobs

In OpenAI mode, the gateway holds an open HTTP connection and polls until the job
completes (or 408 timeout). Under concurrent load with long audio files, this ties up
HTTP worker threads for the full transcription duration. The gateway has no mechanism
to shed load or defer slow OpenAI-mode requests to a separate queue.

This is a known trade-off of the synchronous-compatibility design, but it creates a risk
of cascading latency under load — short jobs queue behind slow ones at the HTTP level.

**Feasibility**: 🔧 New capability
**Remedy**: Hard. Proper mitigation requires a dedicated async-await loop separate from
the HTTP thread pool (FastAPI's background task machinery helps here, but the polling
loop still holds an HTTP connection). A `202 + polling` approach won't satisfy OpenAI
SDK clients expecting a synchronous response.

---

### B-8 · `sk-` prefix API keys accepted without validation

The API compatibility doc notes that `Authorization: Bearer sk-xxx` keys are accepted.
The `sk-` prefix is OpenAI's production key prefix; a misconfigured client that accidentally
sends its real OpenAI key to a Dalston instance will be silently accepted and charged
against the Dalston account's rate limits, with no error message explaining the mismatch.

**Feasibility**: ✅ Current
**Remedy**: Easy. Detect `sk-` prefixed keys and return an informative 401 error suggesting
the user is using an OpenAI key against a Dalston endpoint. (Alternatively, document this
explicitly as intentional for migration ergonomics and keep the current behaviour.)

---

## Remediation Roadmap

### Tier 1 — Easy wins (gateway-only changes, < 1 day each)

| ID | Gap | Change |
|----|-----|--------|
| G-6 | `diarized_json` silent fallback | Add enum value + formatter branch |
| G-7 | Missing `usage` field | Add to response models, populate from transcript |
| G-8 | `temperature=0` not forwarded | Change `> 0` to `is not None` |
| G-12 | `conversation.item.created` not emitted | Emit in commit handler |
| G-13 | `previous_item_id` missing | Track in `OpenAISessionState` |
| G-14 | VAD events missing `item_id` | Include in translated events |
| G-16 | Sparse `transcription_session.created` | Extend with defaults |
| B-2 | Rate-limit headers absent | Response middleware |
| B-3 | `model` not in response | Add field, populate from job |
| B-4 | `prompt` length not validated | Add char/token check |
| B-8 | `sk-` key warning | Return descriptive 401 |

### Tier 2 — Medium (multi-file, up to 1 week each)

| ID | Gap | Change |
|----|-----|--------|
| G-3 | `chunking_strategy: "auto"` acceptance | Accept + validate parameter (no-op for now) |
| G-4 | `known_speaker_names` | Gateway param → job parameter → merge engine relabelling |
| G-11 | PCM16 24 kHz mismatch | Resampling step in realtime workers |
| G-15 | `turn_detection` params ignored | Extend worker URL protocol + VAD init |
| G-2 + G-18 | `logprobs` and real quality signals | Engine output schema + pipeline types + formatter |
| B-1 | `prompt` → vocabulary mismatch | Canonicalise mapping, unify batch/realtime paths |
| B-5 | Large URL bypasses size limit | Stream-download with early abort |

### Tier 3 — Hard (new subsystems, > 1 week each)

| ID | Gap | Change |
|----|-----|--------|
| G-1 / G-9 | SSE streaming for batch endpoints | Engine pub/sub events + gateway SSE handler |
| G-5 | `known_speaker_references` | Speaker embedding engine + enrollment pipeline stage |
| G-17 | `noise_reduction` | Pre-processing stage in realtime workers |
| B-7 | Sync mode scalability | Dedicated async HTTP layer for long-running OAI requests |

---

## Summary

| Category | Count |
|----------|-------|
| Easy closable with current capabilities | 11 |
| Closable with engine/protocol changes | 7 |
| Hard / new subsystem | 4 |
| Blind spots identified | 8 |
| Not feasible / deliberate deviation | 0 |

No gap is architecturally permanent. All deviations are either implementation cost
decisions or are intentional Dalston extensions that do not break OpenAI spec compliance.
