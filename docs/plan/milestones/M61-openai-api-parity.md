# M61: OpenAI Speech-to-Text API Parity

| | |
|---|---|
| **Goal** | Close the actionable gaps between Dalston's OpenAI-compatible STT API and the public OpenAI STT docs as of **March 8, 2026** |
| **Duration** | Phase 0: 1 week · Phase 1: 2 weeks · Phase 2: 4 weeks · Phase 3: 4 weeks |
| **Dependencies** | M38 (OpenAI compat base), M48 (realtime routing), M56 (lite pipeline) |
| **Deliverable** | A pinned `openai-python` contract suite plus docs/traces prove Dalston interoperates with the supported OpenAI STT surface, with exact request validation, exact response shapes, and spec-correct realtime behavior |
| **Status** | Not started |
| **Gap Reference** | [`docs/specs/openai/PARITY_GAPS.md`](../../specs/openai/PARITY_GAPS.md) |

## User Story

> *"As a developer migrating from OpenAI, I can point the OpenAI SDK at Dalston and get the same request validation, response shapes, and realtime event flow for the STT features Dalston claims to support."*

> *"As a realtime client sending spec-compliant 24 kHz PCM16 audio, I get correct transcription rather than slow-motion, pitch-shifted garbage."*

---

## Scope Lock

This milestone is pegged to the public OpenAI STT documentation available on **March 8, 2026**:

- Speech-to-text guide
- Create transcription API reference
- Create translation API reference
- Realtime transcription guide
- Rate limits guide

The compatibility contract for this milestone has three sources:

- Public OpenAI docs for the intended product surface
- The official `openai-python` SDK for the concrete client request/response contract
- Recorded traces against the real OpenAI API for runtime behavior the SDK does not
  fully specify, especially realtime event ordering and session payload nuances

When docs and SDK wording are ambiguous, Dalston should match the pinned
`openai-python` surface for request serialization, response parsing, and header
behavior. When the SDK does not fully describe runtime semantics, use real API traces.

The OpenAI docs are currently inconsistent in a few places, especially on realtime
request/session shapes. This milestone therefore starts by freezing a **docs-backed,
SDK-backed, trace-backed compatibility contract** before implementation.

This milestone's analysis companion is
[PARITY_GAPS.md](../../specs/openai/PARITY_GAPS.md).

---

## Principles

1. **Exact public surface on OpenAI routes**
   Dalston must not invent new request parameters, new response fields, or Dalston-only
   protocol extensions on `/v1/audio/*`, `/v1/realtime`, and
   `/v1/realtime/transcription_sessions`.

2. **Single compatibility table**
   Model support, endpoint support, allowed response formats, and optional parameters
   must come from one authoritative capability table. No ad hoc regex validation.

3. **Exact schema beats "helpful" approximation**
   If OpenAI defines a field shape, we match it. If Dalston cannot produce that shape
   yet, we reject the unsupported combination explicitly instead of returning something
   "close enough".

4. **Prompt is prompt, not hotwords**
   OpenAI's free-text priming prompt must be preserved through the stack. Gateway-level
   conversion into Dalston `vocabulary` terms is the wrong abstraction.

5. **Model-gated behavior**
   Response formats and parameters are not universally valid across OpenAI STT models.
   Validation must be model-aware.

6. **Dual-shape realtime tolerance**
   Because the current OpenAI realtime docs are inconsistent, Dalston should accept the
   documented request/session variants that current official SDKs emit, then normalize
   them internally.

7. **Executable SDK parity gate**
   Parity is not complete until the pinned `openai-python` integration suite passes
   against Dalston, including parsed responses, raw headers, and realtime session setup.

---

## Problem

M38 established the basic OpenAI-compatible routes, but the remaining work is not just
"missing fields". The deeper issue is that Dalston's current compatibility layer is
anchored to an older OpenAI STT surface.

### Current Gaps

```text
SPEC DRIFT
───────────────────────────────────────────────────────────────
  ├── Model validation is based on open-ended regexes, not the
  │   currently documented OpenAI STT model/capability matrix
  ├── Translation support is hardcoded to whisper-1
  ├── Realtime support is hardcoded to a narrow, older model list
  └── Realtime implementation only accepts the old
      transcription_session.* request shape

BATCH CORRECTNESS BUGS
───────────────────────────────────────────────────────────────
  ├── diarized_json falls through to plain json
  ├── temperature=0 is dropped
  ├── prompt is passed as raw string or split into fake hotwords
  ├── verbose_json quality fields are hardcoded sentinels
  ├── OpenAI rate-limit header names are not returned
  ├── OpenAI URL requests do not apply the 25 MB ceiling early
  └── sk- keys fail with a generic Dalston auth error

RESPONSE SHAPE DRIFT
───────────────────────────────────────────────────────────────
  ├── Milestone M38-era assumptions about diarized_json are stale
  ├── usage shape is model-dependent, not always audio_seconds
  ├── top-level model field must not be invented where OpenAI
  │   does not define one
  └── supported parameter/format combinations vary by model

REALTIME CORRECTNESS BUGS
───────────────────────────────────────────────────────────────
  ├── pcm16 is treated as 16 kHz instead of OpenAI's 24 kHz
  ├── conversation.item.created is missing on commit
  ├── input_audio_buffer.committed lacks previous_item_id
  ├── speech_started / speech_stopped lack item_id
  ├── item rotation is too simplistic for the OpenAI item graph
  └── turn_detection tuning fields are accepted then discarded

NEW CAPABILITY GAPS
───────────────────────────────────────────────────────────────
  ├── stream=true SSE for batch transcription / translation
  ├── include=item.input_audio_transcription.logprobs
  ├── chunking_strategy current request shape
  ├── known_speaker_names
  ├── known_speaker_references
  ├── realtime session-create REST endpoint used by official SDKs
  └── realtime noise_reduction pre-processing

TEST CONTRACT GAPS
───────────────────────────────────────────────────────────────
  ├── only a thin Python SDK smoke test exists today
  ├── no SDK coverage asserts OpenAI headers via raw responses
  ├── no SDK coverage exercises streaming response parsing
  └── no SDK coverage exercises realtime session creation
```

---

## Phase 0: Contract Lock (Week 1)

This phase is mandatory. We should not "fix parity" against moving or ambiguous
documentation without freezing the exact target first.

### 0.1: Pin the Python SDK contract and capture traces

Pin one `openai-python` version for the primary compatibility gate and record request
and response traces for the exact methods Dalston intends to support:

- `audio.transcriptions.create(...)`
- `audio.translations.create(...)`
- `audio.transcriptions.with_raw_response.create(...)`
- Batch streaming behavior through `stream=True` / streaming-response helpers
- `beta.realtime.transcription_sessions.create(...)`
- Realtime transcription buffer commit flow

Store these as locked fixtures under `tests/integration/openai_fixtures/`.

Also record a small set of real OpenAI traces for behaviors the SDK does not fully
encode, especially realtime event sequencing and session payload details.

### 0.2: Build a single capability table

Create an authoritative `OPENAI_STT_CAPABILITIES` table in the gateway layer.

Seed it from the docs, generated SDK parameter/response types, and recorded traces with
at least the currently documented STT family:

- `whisper-1`
- `gpt-4o-transcribe`
- `gpt-4o-mini-transcribe`
- `gpt-4o-transcribe-diarize`
- Any currently documented dated aliases
- Any currently documented realtime guide alias such as `gpt-4o-transcribe-latest`

Each row must declare:

- Supported endpoints: `transcriptions`, `translations`, `realtime`
- Allowed response formats
- Whether diarized output is supported
- Whether `usage` is audio-seconds-based or token-based
- Whether `chunking_strategy` is accepted
- Whether `known_speaker_names` is accepted
- Whether `known_speaker_references` is accepted
- Whether `include=item.input_audio_transcription.logprobs` is accepted

### 0.3: Freeze canonical request and response schemas

For each supported model/format pair, define the exact OpenAI request and response
schema Dalston must accept or emit. This is especially important for:

- multipart request field names and shapes as serialized by `openai-python`
- `json`
- `verbose_json`
- `diarized_json`
- Translation response variants
- Batch raw-response header behavior
- Batch streaming event shapes
- Realtime transcription-session create payloads
- Realtime session-created / item events

### 0.4: Define the SDK contract suite up front

Add a dedicated SDK contract test layer that uses the official Python SDK against
Dalston's OpenAI-compatible surface.

- Add a pinned-version integration suite under `tests/integration/`
- Keep `tests/e2e/test_openai_sdk.py` as a smaller live-stack smoke test
- Make the integration suite the primary compatibility gate for:
  - request serialization
  - response parsing
  - raw headers
  - streaming event parsing
  - realtime session creation

**Best solution**

Prefer fast SDK integration tests over live-stack-only smoke tests. The cleanest setup is
to run Dalston in a deterministic test harness and point `openai.OpenAI(base_url=...)`
at it, then reserve full Docker e2e coverage for a narrow smoke path.

### Phase 0 Checkpoint

- [ ] Pinned `openai-python` version chosen for the compatibility gate
- [ ] SDK request/response traces checked into test fixtures
- [ ] `OPENAI_STT_CAPABILITIES` defined from docs + traces
- [ ] Broad regex-only model validation retired as the source of truth
- [ ] Exact request/response schemas frozen for supported model/format pairs
- [ ] SDK contract suite skeleton checked in with locked fixtures

---

## Phase 1: Gateway Correctness (Weeks 2-3)

All Phase 1 items are gateway-owned and should land before engine or worker changes.

### 1.1: Replace regex-driven validation with capability-table validation

**Current problem**

Current validation in [openai_audio.py](../../../dalston/gateway/api/v1/openai_audio.py)
accepts broad future-looking patterns. That is convenient, but it is not parity:
it allows undocumented models and cannot express per-model feature gates.

**Remedy**

- Keep lightweight alias/pattern helpers only for compatibility aliases captured in Phase 0
- Make the capability table authoritative for:
  - model validity
  - endpoint validity
  - allowed response formats
  - allowed optional parameters

**Best solution**

Centralize all OpenAI request validation in one place. `transcriptions`, `translations`,
and realtime must all call the same model-aware validator.

### 1.2: Fix `diarized_json` using the exact OpenAI schema

**Current problem**

Dalston documents `diarized_json` in the transcription form description but does not
actually support it today.

**Remedy**

- Add `diarized_json` to the supported response format enum
- Format the response using the **current OpenAI diarized schema from Phase 0**
- Do **not** use the old `utterances[]` proposal from the previous version of this milestone
- Do **not** invent a top-level `model` field if OpenAI does not define one

**Best solution**

Build `diarized_json` from Dalston's merged transcript plus speaker metadata:

- `text`
- `segments`
- `speakers`
- `usage` only when the target model/format defines it

Unsupported combinations, such as requesting diarized output on a model that does not
support it, must return an OpenAI-shaped 400 instead of silently falling back.

### 1.3: Emit exact `usage` objects, not one universal shape

**Current problem**

The older milestone assumed `usage={"type":"audio","audio_seconds":...}` for all STT
responses. The current OpenAI STT surface is model-dependent.

**Remedy**

- Introduce model-aware usage builders
- Never emit fields that are not defined for the selected model family
- If a supported model requires exact token accounting and Dalston cannot compute it yet,
  that model/format pair stays unsupported until accounting is implemented

**Best solution**

Hide usage construction behind the capability table:

- audio-seconds-based usage where the docs require it
- token-based usage where the docs require it
- no speculative fallback fields

### 1.4: Fix `temperature=0` forwarding

**Current problem**

Batch transcription and translation currently drop explicit `temperature=0`.

**Remedy**

Change the guard from:

```python
if temperature is not None and temperature > 0:
```

to:

```python
if temperature is not None:
```

This applies to both `transcriptions` and `translations`.

### 1.5: Enforce prompt length with a token-aware validator

**Current problem**

The prior milestone proposed a `~900 characters` heuristic. That is not robust.

**Remedy**

- Validate the OpenAI prompt limit with a token-aware counter when possible
- Use a conservative fallback only if tokenization support is unavailable
- Return an OpenAI-shaped 400 with `param="prompt"`

**Best solution**

Add a small, isolated prompt validation helper in the gateway. Do not tie validation to
how Dalston internally consumes prompts.

### 1.6: Return OpenAI rate-limit headers on success and 429

**Current problem**

Dalston already returns legacy `X-RateLimit-*` headers on some 429s, but it does not
consistently emit the OpenAI header names, and it does not attach them to successful
OpenAI responses.

**Remedy**

- Reuse the existing rate-limit dependency flow in
  [dependencies.py](../../../dalston/gateway/dependencies.py)
- Add a small header helper that attaches:
  - OpenAI header names
  - existing legacy names if we want to preserve Dalston compatibility
- Apply it to both successful OpenAI responses and 429 responses

**Best solution**

Do not add a new middleware layer for this. The existing request-limit and concurrent-limit
checks already have the required data.

### 1.7: Diagnose accidental `sk-` key usage clearly

**Current problem**

HTTP and WebSocket auth currently reject OpenAI keys with a generic invalid-key error.

**Remedy**

Add a targeted auth branch before ordinary invalid-key handling:

- If the presented key starts with `sk-`
- Return or close with an OpenAI-shaped diagnostic saying the caller appears to be using
  an OpenAI API key against a Dalston endpoint

### 1.8: Apply the 25 MB OpenAI ceiling during URL ingestion

**Current problem**

The older milestone assumed URL downloads ignored size completely. In reality,
[audio_url.py](../../../dalston/gateway/services/audio_url.py) already enforces size
limits during streaming, but OpenAI-mode requests do not pass the 25 MB ceiling down.

**Remedy**

- Extend `AudioIngestionService.ingest(...)` to accept `max_bytes`
- For OpenAI routes, pass `OPENAI_MAX_FILE_SIZE`
- Reuse the existing downloader's:
  - `Content-Length` short-circuit
  - streaming byte-count guard

**Best solution**

Do not rewrite the downloader. Thread the correct limit through the existing ingestion path.

### 1.9: Align translation validation with the capability table

**Current problem**

Current translation handling hardcodes `whisper-1` and a hand-maintained response-format
set in [openai_translation.py](../../../dalston/gateway/api/v1/openai_translation.py).

**Remedy**

- Route translation model validation through the same capability table as transcription
- Gate response formats and optional parameters by model
- Keep OpenAI-route validation exact even if Dalston-native routes support more formats

### Phase 1 Checkpoint

- [ ] Capability-table validation is the only source of truth
- [ ] `diarized_json` returns the exact current OpenAI schema
- [ ] `usage` is model-aware and exact
- [ ] `temperature=0` is forwarded in transcription and translation
- [ ] Prompt length is validated without a naive character heuristic
- [ ] OpenAI rate-limit headers are attached on success and 429
- [ ] `sk-` keys return a targeted diagnostic
- [ ] OpenAI URL requests apply the 25 MB ceiling during download
- [ ] Translation validation is driven by the same capability table as transcription

---

## Phase 2: Batch Pipeline Fidelity (Weeks 4-7)

These items cross gateway, pipeline, and engine boundaries.

### 2.1: Preserve `prompt` as a first-class field end-to-end

**Current problem**

The current code sends OpenAI `prompt` into Dalston `vocabulary`, sometimes as a raw
string and sometimes as a comma-split list. That is semantically wrong.

**Remedy**

- Add a first-class `prompt` field to the internal transcription config
- Preserve the original string from gateway to engine
- Let engine adapters decide how to consume it:
  - `faster-whisper`: `initial_prompt`
  - other engines: native prompt if supported
  - hotword-only engines: explicit adapter fallback or explicit unsupported error

**Best solution**

Add engine capability metadata such as `supports_prompt`. When an OpenAI request includes
`prompt`, prefer engines that can consume it natively instead of downgrading the semantics
in the gateway.

### 2.2: Support `known_speaker_names` through merge-time relabeling

**Current problem**

This parameter is feasible with the current architecture, but it has to be implemented
against the exact current `diarized_json` output, not the older `utterances[]` proposal.

**Remedy**

- Accept and validate `known_speaker_names`
- Pass it through job parameters to the merge stage
- Relabel generic speaker IDs in order of first appearance
- Update both segment speaker labels and speaker metadata in the final transcript

**Best solution**

Perform the relabeling in the merge stage so the canonical transcript written to storage
already reflects the resolved names.

### 2.3: Accept the current `chunking_strategy` request shape

**Current problem**

The older milestone assumed `chunking_strategy="auto"` as a bare string. The current docs
use a richer shape and model-gated support.

**Remedy**

- Parse the current docs-backed `chunking_strategy` payload from Phase 0
- Validate it by model
- For the currently supported automatic strategy, accept and map it to Dalston's default
  segmentation behavior
- Reject unsupported strategies explicitly

**Best solution**

Treat `chunking_strategy` as a model-gated request object, not a free-form string.

### 2.4: Preserve real segment quality metadata in the pipeline

**Current problem**

`verbose_json` currently returns hardcoded `avg_logprob`, `compression_ratio`,
`no_speech_prob`, and `tokens`.

**Remedy**

- Extend the internal typed segment schema to carry quality metadata
- Capture real values in engines that produce them
- Preserve them through merge
- Emit them in `verbose_json`

**Best solution**

Separate two concerns:

- real segment-quality metadata for `verbose_json`
- OpenAI `include=...logprobs` support, which is a distinct feature

### 2.5: Implement model-gated `include=item.input_audio_transcription.logprobs`

**Current problem**

This is not the same thing as `avg_logprob` on a segment. The docs now describe an
explicit `include` value for transcription logprobs.

**Remedy**

- Validate `include` against the capability table
- Add a dedicated internal flag such as `include_transcription_logprobs`
- Extend engine outputs only where the chosen engine can actually provide the needed data
- Reject the request if the selected model advertises the feature but Dalston cannot
  currently supply the exact shape

**Best solution**

Keep this feature separate from the generic `verbose_json` quality cleanup. They solve
different parity gaps.

### 2.6: Fill model-aware usage for GPT-4o-family STT if required by Phase 0

**Current problem**

If the Phase 0 capability table marks a supported model as requiring token-based usage,
Phase 1's structural usage builder is not enough.

**Remedy**

- Add exact accounting where required by the docs
- Keep model/format pairs unsupported until that accounting is exact

### Phase 2 Checkpoint

- [ ] Prompt is preserved as free text through the stack
- [ ] OpenAI routes no longer convert prompt into fake gateway hotwords
- [ ] `known_speaker_names` relabels canonical transcript speaker metadata
- [ ] `chunking_strategy` accepts the current request shape and is model-gated
- [ ] `verbose_json` uses real segment-quality metadata where available
- [ ] `include=item.input_audio_transcription.logprobs` is validated and implemented separately
- [ ] Any supported GPT-4o-family STT model has exact usage accounting

---

## Phase 3: Realtime Protocol Fidelity (Weeks 8-11)

These items cover correctness of the OpenAI realtime surface that Dalston already exposes
or must expose for official SDK parity.

### 3.0: Add the SDK-visible realtime session-create REST endpoint

**Current problem**

The official Python SDK exposes `client.beta.realtime.transcription_sessions.create(...)`.
Dalston currently focuses on the WebSocket route and does not treat the REST session-create
flow as a first-class parity target.

**Remedy**

- Implement the exact `/v1/realtime/transcription_sessions` contract frozen in Phase 0
- Reuse the same capability table and session normalization used by the WebSocket route
- Return `client_secret` or related fields only where the frozen contract requires them

**Best solution**

Do not build a separate realtime compatibility stack for REST and WebSocket setup. One
session-config normalizer and one schema builder should drive both.

### 3.1: Accept both current realtime request/session shapes

**Current problem**

Dalston only accepts `transcription_session.update` plus the older flat session object,
while the current OpenAI docs and guides are inconsistent and some clients use newer
`session.update` / nested `audio.input.*` shapes.

**Remedy**

- Accept both current documented request shapes
- Normalize them into one internal `OpenAIRealtimeSessionConfig`
- Preserve a single canonical internal representation for:
  - audio format
  - language
  - prompt
  - turn detection
  - noise reduction

**Best solution**

Use the Phase 0 SDK traces to decide the canonical outgoing event set, but accept both
incoming variants on day one.

### 3.2: Fix the realtime item graph

**Current problem**

Current item tracking in
[openai_realtime.py](../../../dalston/gateway/api/v1/openai_realtime.py) is too simple.
It misses required events and rotates IDs in a way that can drift from OpenAI's item graph.

**Remedy**

- Introduce explicit session state for:
  - current pending item
  - last committed item
  - previous item ID
- On commit:
  - emit `conversation.item.created`
  - emit `input_audio_buffer.committed` with `previous_item_id`
- On VAD events:
  - include `item_id`
- Ensure transcript delta/final events attach to the correct committed item
- Remove the current double-rotation bug

**Best solution**

Stop treating `current_item_id` as a single mutable cursor. Model the committed user item
explicitly.

### 3.3: Fix `pcm16` to mean 24 kHz on the OpenAI route

**Current problem**

Dalston maps realtime `pcm16` to 16 kHz today, which is wrong for the OpenAI route.

**Remedy**

- Keep the public OpenAI route spec-accurate: `pcm16` means 24 kHz PCM16
- Pass both `client_sample_rate` and worker `sample_rate`
- Resample internally before VAD / ASR when needed

**Best solution**

Do **not** advertise a Dalston-only `pcm16_16k` format on `/v1/realtime`.
If Dalston-native clients need a 16 kHz contract, that belongs on Dalston-native
realtime endpoints, not the OpenAI-compatible one.

### 3.4: Forward full `turn_detection` tuning to the worker

**Current problem**

Dalston currently treats `turn_detection` as on/off and drops the tuning values.

**Remedy**

- Forward:
  - `threshold`
  - `silence_duration_ms`
  - `prefix_padding_ms`
- Map them onto the realtime VAD implementation:
  - threshold -> speech threshold
  - silence duration -> endpoint threshold
  - prefix padding -> lookback buffer / pre-roll configuration

**Best solution**

The current VAD implementation already has lookback buffering. Use that to model
`prefix_padding_ms` rather than inventing a separate ad hoc buffer.

### 3.5: Emit exact session-created / session-updated payloads

**Current problem**

Dalston emits a sparse session-created object today.

**Remedy**

- Emit the exact docs-backed session object frozen in Phase 0
- Include current defaults for supported config
- Echo unsupported `noise_reduction` as `null` where the OpenAI shape requires it
- Do not invent unrelated fields

**Important note**

If `client_secret` turns out to belong to a separate session-creation REST flow rather
than the WebSocket-created event, do **not** fake it in the WebSocket payload. Add the
separate endpoint only if the SDK traces prove it is required.

### 3.6: Keep `noise_reduction` as a deferred capability unless we really implement it

**Current problem**

OpenAI exposes noise-reduction config, but Dalston does not currently run a realtime
denoise stage.

**Remedy**

- Parse and preserve the field in session config
- Echo it as unsupported / `null` in the emitted session payload if that is what the
  Phase 0 contract requires
- Do not claim active noise reduction until a real pre-processing stage exists

### Phase 3 Checkpoint

- [ ] `/v1/realtime/transcription_sessions` matches the pinned SDK contract
- [ ] Both current documented realtime request/session shapes are accepted
- [ ] Realtime item graph events match the frozen Phase 0 contract
- [ ] `pcm16` is treated as 24 kHz on the OpenAI route
- [ ] Internal resampling happens before VAD / ASR where needed
- [ ] `turn_detection` tuning reaches the worker VAD config
- [ ] Session-created / updated payloads are exact and modelled from SDK traces
- [ ] `noise_reduction` is not falsely advertised as implemented

---

## Phase 4: Deferred New Subsystems

These are legitimate parity gaps, but they require new infrastructure or new signal
extraction. They should not be mixed into the gateway-correctness work.

### 4.1: `stream=true` SSE for batch transcription / translation

Requires a new partial-result path from engine/orchestrator to gateway.

### 4.2: `known_speaker_references`

Requires speaker enrollment / embedding support, likely as a new diarization-side capability.

### 4.3: Realtime `noise_reduction` pre-processing

Requires a real DSP / denoise stage in the realtime worker, not just field echoing.

### 4.4: Pub/sub wakeup for synchronous OpenAI-mode batch requests

This may still be a good latency or scalability improvement, but it is **not**
an OpenAI parity requirement. The current synchronous wait path in
[polling.py](../../../dalston/gateway/services/polling.py) is already async.
Treat this as a separate performance milestone unless measurements justify it.

### Phase 4 Checkpoint

- [ ] SSE work is scoped as a new subsystem, not a gateway tweak
- [ ] Voice-print speaker identification is scoped separately from speaker-name relabeling
- [ ] Realtime denoise work is scoped as real DSP, not a placeholder config echo
- [ ] Polling-to-pubsub wakeup is tracked as performance work, not parity work

---

## Tests

### Required Integration Coverage

- Capability-table validation for transcriptions, translations, and realtime
- Exact response-shape fixtures for:
  - `json`
  - `verbose_json`
  - `diarized_json`
- Prompt length validation
- `temperature=0`
- OpenAI rate-limit headers
- `sk-` key diagnostics
- OpenAI URL size-limit enforcement
- Realtime transcription-session create REST endpoint
- Realtime item graph / commit flow
- Realtime 24 kHz PCM handling
- Realtime dual-shape request acceptance

### Required SDK Coverage

- A pinned-version Python SDK contract suite under `tests/integration/`
- `OpenAI(base_url=...)` batch transcription coverage for:
  - `json`
  - `verbose_json`
  - `diarized_json` where supported
  - validation failures for unsupported model/format combinations
- `OpenAI(base_url=...)` translation coverage for the exact supported model/format matrix
- `with_raw_response` coverage asserting:
  - OpenAI header names
  - status codes
  - content types
- Lock batch streaming fixtures in Phase 0, but keep `stream=true` execution tests gated
  behind Phase 4 until SSE support actually exists
- Realtime REST coverage for `client.beta.realtime.transcription_sessions.create(...)`
- Realtime connection/update/append/commit/final-transcript coverage driven by SDK
  traces and the frozen Phase 0 event fixtures
- A smaller live-stack smoke test in `tests/e2e/test_openai_sdk.py`

### CI Policy

- Pin the main CI compatibility gate to one known `openai` version
- Optionally run a non-blocking canary job against the latest `openai` release to catch
  upstream drift early

---

## Files Expected To Change

| File | Phase | Change |
|------|-------|--------|
| `pyproject.toml` | 0 | Add pinned `openai` test/dev dependency for the contract suite |
| `dalston/gateway/api/v1/openai_audio.py` | 0, 1 | Capability table, model-aware validation, exact response-shape formatters, usage builders |
| `dalston/gateway/api/v1/transcription.py` | 1, 2 | Capability-driven validation, `temperature=0`, prompt passthrough, `chunking_strategy`, `known_speaker_names`, `include` |
| `dalston/gateway/api/v1/openai_translation.py` | 1, 2 | Capability-driven validation, `temperature=0`, prompt passthrough, model-aware formats |
| `dalston/gateway/api/v1/openai_realtime.py` | 0, 3 | REST session-create endpoint, dual-shape request normalization, item-graph correctness, exact session payloads, 24 kHz handling |
| `dalston/gateway/dependencies.py` | 1 | OpenAI rate-limit header attachment in existing dependency flow |
| `dalston/gateway/middleware/auth.py` | 1 | `sk-` key diagnostics for HTTP and WebSocket auth |
| `dalston/gateway/services/ingestion.py` | 1 | Thread `max_bytes` through OpenAI-mode ingestion |
| `dalston/gateway/services/audio_url.py` | 1 | Likely no rewrite; reuse existing size guards |
| `dalston/common/pipeline_types.py` | 2 | First-class prompt field; richer segment-quality fields |
| `dalston/orchestrator/dag.py` | 2 | Pass prompt and `known_speaker_names` through the DAG |
| `engines/stt-transcribe/*/engine.py` | 2 | Consume prompt natively where supported; preserve quality fields |
| `engines/stt-merge/final-merger/engine.py` | 2 | Apply `known_speaker_names` relabeling and preserve speaker metadata |
| `dalston/realtime_sdk/base.py` | 3 | Parse extra worker query params such as `client_sample_rate` and VAD tuning |
| `dalston/realtime_sdk/session.py` | 3 | Internal resampling and VAD tuning application |
| `dalston/realtime_sdk/vad.py` | 3 | Prefix-padding / lookback mapping if needed |
| `tests/integration/openai_fixtures/*` | 0 | Locked docs/SDK/real-API compatibility fixtures |
| `tests/integration/test_openai_api.py` | 0, 1, 2 | Exact response-shape and request-validation coverage |
| `tests/integration/test_openai_realtime_api.py` | 0, 3 | Realtime dual-shape and item-graph coverage |
| `tests/integration/test_openai_sdk_contract.py` | 0, 1, 2, 3 | Pinned official Python SDK compatibility suite |
| `tests/e2e/test_openai_sdk.py` | 0, 1, 2, 3 | Narrow live-stack smoke test using the official Python SDK |

---

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| OpenAI docs continue to drift during implementation | Freeze Phase 0 fixtures and capability table with an explicit date stamp |
| Gateway starts lying about support by returning approximate shapes | Make unsupported model/format pairs explicit 400s until exact parity is implemented |
| SDK releases drift faster than the docs | Pin one `openai` version in CI and keep an optional latest-version canary |
| Prompt passthrough exposes engine capability differences | Add `supports_prompt` capability metadata and route OpenAI prompt requests accordingly |
| Realtime docs remain inconsistent | Accept both documented input shapes and lock outgoing behavior to SDK-trace fixtures |
| 24 kHz resampling hurts quality | Benchmark 24 kHz source vs resampled output; keep the public contract spec-correct regardless |
| Token-based usage is harder than expected for some models | Leave the affected model/format pair unsupported until exact accounting exists |

---

## Out of Scope

- Extending Dalston-native APIs to mimic OpenAI surface details
- Adding speculative support for future OpenAI models not present in the Phase 0 contract
- Replacing the async polling path purely for performance without parity justification
