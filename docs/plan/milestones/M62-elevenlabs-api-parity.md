# M62: ElevenLabs Speech-to-Text API Parity

| | |
|---|---|
| **Goal** | Close the actionable gaps between Dalston's ElevenLabs-compatible STT API and the public ElevenLabs STT docs as of **March 8, 2026** |
| **Duration** | Phase 0: 1 week; Phase 1: 2 weeks; Phase 2: 4 weeks; Phase 3: 4 weeks |
| **Dependencies** | M08 (ElevenLabs compat base), M45 (security hardening), M48 (realtime routing) |
| **Deliverable** | A pinned `elevenlabs-python` contract suite plus docs/traces prove Dalston interoperates with the supported ElevenLabs STT surface, with exact request validation, exact response shapes, correct realtime behavior, and browser-safe auth that matches the published contract |
| **Status** | Not started |
| **Gap Reference** | [`docs/specs/elevenlabs/PARITY_GAPS.md`](../../specs/elevenlabs/PARITY_GAPS.md) |

## User Story

> *"As a developer migrating from ElevenLabs, I can point the ElevenLabs SDK at Dalston by changing only the base URL and get the same request validation, response shapes, and realtime protocol for the STT features Dalston claims to support."*

> *"As a browser application, I can open a realtime STT session with a short-lived single-use token instead of embedding a long-lived API key in client-side JavaScript."*

> *"As an async caller using `webhook=true`, I get the documented webhook delivery semantics instead of an acknowledgement that never results in a callback."*

---

## Scope Lock

This milestone is pegged to the public ElevenLabs STT surface available on **March 8, 2026**:

- [Speech-to-text API reference](https://elevenlabs.io/docs/api-reference/speech-to-text)
- [Realtime speech-to-text API reference](https://elevenlabs.io/docs/api-reference/speech-to-text/realtime)
- [Single-use token API reference](https://elevenlabs.io/docs/api-reference/tokens/create)
- [Speech-to-text capabilities](https://elevenlabs.io/docs/capabilities/speech-to-text)
- [Model and rate-limit docs](https://elevenlabs.io/docs/models#rate-limits)
- [Official `elevenlabs-python` SDK](https://github.com/elevenlabs/elevenlabs-python)

The public docs are not perfectly consistent in every example, especially around model
IDs on the batch side. Where docs and examples drift, we should freeze the contract from:

1. The latest official API reference pages.
2. The current official Python SDK request shapes.
3. Captured SDK traces against the live public contract.

This milestone's analysis companion is
[PARITY_GAPS.md](../../specs/elevenlabs/PARITY_GAPS.md).

---

## Principles

1. **Exact public surface on ElevenLabs routes**
   Dalston must not invent Dalston-only request parameters, response bodies, or auth
   flows on `/v1/speech-to-text/*` and `/v1/single-use-token/*`.

2. **Reuse existing Dalston subsystems**
   We already have job deletion, ephemeral token issuance, webhook endpoint CRUD, and a
   delivery worker. Parity work should adapt those subsystems to the ElevenLabs contract,
   not build parallel infrastructure.

3. **No deliberate incompatibility phases**
   If ElevenLabs currently documents a feature as supported, such as `ulaw_8000`, we do
   not ship an intentional reject-first phase. We either support it correctly or reject
   the entire unsupported combination explicitly before claiming parity.

4. **Separate batch and realtime capability tables**
   Batch and realtime model IDs are no longer safely interchangeable. Validation must be
   endpoint-specific and driven by one docs-backed compatibility table.

5. **Exact request locations matter**
   Query-vs-form placement is part of the public API. Examples: batch `enable_logging`
   belongs on the query string, realtime auth uses `?token=`, and single-use token
   issuance uses the documented ElevenLabs path rather than a Dalston-only alias.

6. **Exact schema beats heuristics**
   `words[].type`, `words[].characters`, `words[].logprob`, and `entities` should come
   from exact transcript/token data. We should not approximate ElevenLabs response shapes
   with silence-gap heuristics or placeholder payloads.

7. **Executable SDK parity gate**
   The official `elevenlabs-python` SDK should be part of the required test contract.
   Mock-heavy route tests are still useful, but they are not enough to catch subtle
   multipart, response-parsing, auth, and realtime-client drift.

---

## Problem

M08 established the ElevenLabs-compatible routes, but Dalston's compatibility layer is
still anchored to older assumptions about the public contract.

### Current Gaps

```text
CONTRACT DRIFT
- Batch and realtime model handling is still M08-era and not locked to the current
  docs + SDK surface
- The batch handler is missing documented request fields such as entity_detection,
  file_format, webhook_id, and webhook_metadata
- Batch enable_logging is treated as a form concern instead of the current
  query-param contract
- Browser auth still revolves around api_key= instead of the published
  single-use token flow with token=

BATCH CORRECTNESS BUGS
- DELETE /transcripts/{id} alias is missing even though Dalston already has a native
  delete path we can reuse
- GET /transcripts/{id} returns a Dalston-only processing object instead of the public
  ElevenLabs behavior
- model_id is silently ignored
- request_id is always null in async responses
- Keyterm validation misses the <=5 word limit
- additional_formats are not returned inline
- entity_detection / entities are absent from the public ElevenLabs-compatible route

REALTIME CORRECTNESS BUGS
- commit_strategy still defaults to "vad" instead of ElevenLabs' documented "manual"
- ulaw_8000 is accepted then forwarded as garbage bytes
- include_language_detection is dropped
- previous_text and VAD tuning parameters are discarded
- session_started does not reflect the effective config
- ElevenLabs error message types collapse to generic error
- The word schema is stale: no logprob, no characters, and no correct spacing tokens

INTEGRATION GAPS
- webhook=true is not wired to Dalston's existing webhook endpoint and delivery
  subsystems
- The existing auth token service is not adapted to the ElevenLabs single-use token
  contract
- 3 GB upload / 2 GB cloud URL size ceilings are not enforced consistently at the gateway
- use_multi_channel and audio_event-style enrichment still require genuine new capability
  work

LOWER-PRIORITY NON-PARITY WORK
- Idempotency-Key support is useful platform work, but it is not an ElevenLabs STT
  parity requirement
- If we expose rate-limit headers, they must be the official ElevenLabs concurrency
  headers, not custom x-ratelimit-*

TEST CONTRACT GAPS
- The current ElevenLabs coverage is still mostly route-level and mock-heavy
- No pinned official Python SDK contract suite exists under `tests/integration/`
- Existing tests still encode stale assumptions such as arbitrary `model_id`
  acceptance and a custom `200 {"status":"processing"}` GET shape
- No SDK coverage currently exercises the single-use token flow or browser-safe realtime
  connection setup
```

---

## Phase 0: Contract Lock (Week 1)

This phase is mandatory. The public docs have drifted since M08, and the SDK is the
right tie-breaker when examples conflict.

### 0.1: Pin the Python SDK contract and capture traces

Pin one `elevenlabs-python` version for the primary compatibility gate and record
request/response traces for the exact methods Dalston intends to support:

- `speech_to_text.convert(...)` in sync and async forms
- `speech_to_text.get(...)`
- `speech_to_text.delete(...)`
- single-use token creation for realtime STT
- realtime session setup, manual commit flow, and `ulaw_8000` input

Store these as locked fixtures under `tests/integration/elevenlabs_fixtures/`.

Also record a small set of raw HTTP/WebSocket traces for runtime details the SDK does not
fully expose, especially polling semantics, webhook payload shapes, and realtime error
event details.

### 0.2: Build one authoritative capability table

Create an `ELEVENLABS_STT_CAPABILITIES` table in the gateway layer with separate
endpoint-specific entries for:

- Batch transcription model IDs accepted by the current docs + SDK
- Realtime model IDs accepted by the current docs + SDK
- Supported request fields and where they live (`query`, `multipart`, `ws_query`,
  `message_body`)
- Supported audio formats
- Supported word payload fields
- Supported webhook and token flows

This must replace the current mix of hardcoded defaults, one-off whitelists, and silent
fallbacks.

### 0.3: Freeze canonical public schemas

For each supported route, lock the exact response shapes Dalston must emit:

- async batch acknowledgement
- completed transcript payload
- GET/DELETE transcript behavior
- webhook payloads for completed and failed jobs
- realtime `session_started`, transcript, and error messages
- single-use token issuance response

### 0.4: Define the SDK contract suite up front

Add a dedicated SDK contract test layer that uses the official Python SDK against
Dalston's ElevenLabs-compatible surface.

- Add a pinned-version integration suite under `tests/integration/`
- Keep a smaller live-stack smoke test under `tests/e2e/`
- Make the integration suite the primary compatibility gate for:
  - request serialization
  - response parsing
  - auth and token flow
  - GET/DELETE behavior
  - realtime connection setup where the SDK exposes it

**Best solution**

Prefer fast SDK integration tests over live-stack-only smoke tests. The cleanest setup is
to run Dalston in a deterministic test harness and point `ElevenLabs(base_url=...)` at
it, then reserve full Docker e2e coverage for a narrow smoke path.

### Phase 0 Checkpoint

- [ ] Pinned `elevenlabs-python` version chosen for the compatibility gate
- [ ] SDK request/response traces checked into fixtures
- [ ] `ELEVENLABS_STT_CAPABILITIES` defined from docs + SDK traces
- [ ] Batch and realtime model IDs separated in validation
- [ ] Canonical transcript, webhook, realtime, and token schemas frozen
- [ ] SDK contract suite skeleton checked in with locked fixtures

---

## Phase 1: Gateway Correctness (Weeks 2-3)

All Phase 1 work should be gateway-owned and should remove current silent divergence
before we expand worker or pipeline behavior.

### 1.1: Replace M08-era validation with capability-table validation

**Current problem**

Validation is still scattered across the handlers and anchored to older assumptions:

- batch `model_id` is accepted then ignored
- realtime defaults still assume `scribe_v1`
- documented request fields are missing or live in the wrong place
- unsupported combinations are sometimes accepted and silently discarded

**Remedy**

- Centralize ElevenLabs request validation behind `ELEVENLABS_STT_CAPABILITIES`
- Validate field location as well as field value
- Reject unsupported combinations with ElevenLabs-shaped 4xx errors instead of silently
  accepting and dropping them

**Best solution**

Make the compatibility table authoritative for both batch and realtime so that parameter
acceptance, model support, and response shape are all driven from one locked contract.

### 1.2: Fix GET and DELETE transcript compatibility by reusing existing job services

**Current problem**

Dalston currently invents a `processing` response for in-flight transcripts and simply
does not expose the ElevenLabs DELETE alias.

**Remedy**

- Remove the Dalston-only processing response from the ElevenLabs route
- Return a transcript only when the transcript is actually materialized
- While a job is still pending, return ElevenLabs-compatible `404` plus `Retry-After`
  rather than a custom body
- Add `DELETE /v1/speech-to-text/transcripts/{id}` as a thin alias over Dalston's
  existing deletion path

**Best solution**

Reuse the existing authorization and deletion machinery in the native transcription API.
Do not add a second soft-delete subsystem or a second artifact-cleanup path just for the
ElevenLabs route.

Terminal failure polling semantics should be finalized from Phase 0 traces. Until that is
locked, the only hard rule is: do not invent a Dalston-only `processing` schema.

### 1.3: Close the easy batch correctness gaps and stop silent drops

**Current problem**

Several easy fields still diverge from the public contract:

- `request_id` is null in async responses
- keyterms do not enforce the published <=5 word limit
- batch `enable_logging` is not modeled at the correct request location
- documented request fields such as `file_format`, `webhook_id`, and
  `webhook_metadata` are not consistently parsed and validated

**Remedy**

- Populate `request_id` from the existing correlation middleware
- Enforce the keyterm word-count limit on both batch and realtime routes
- Accept `enable_logging` on the query string, matching the current ElevenLabs contract
- Parse and validate the full docs-backed batch field inventory

**Best solution**

If a field is documented but not yet supported end-to-end, reject it explicitly. Do not
accept it and quietly throw it away.

### 1.4: Fix the realtime session contract at the gateway boundary

**Current problem**

The realtime route diverges from the current contract before the worker is even involved:

- `commit_strategy` defaults to the wrong value
- `include_language_detection` is not wired
- `session_started` does not reflect the real effective config
- worker and gateway failures collapse to generic `error`

**Remedy**

- Change the default `commit_strategy` to `manual`
- Accept and forward `include_language_detection`
- Emit `session_started` from the normalized effective config
- Map gateway and worker failures to the ElevenLabs error vocabulary

**Best solution**

Only echo fields in `session_started` once they are actually validated and normalized.
Do not echo unsupported VAD knobs as if they were active until they are truly wired
through in Phase 2.

### 1.5: Decode docs-supported telephony audio instead of rejecting it

**Current problem**

`ulaw_8000` is a documented realtime input format, but Dalston currently base64-decodes
the chunk and forwards the raw mu-law bytes as if they were PCM16.

**Remedy**

- Add a small gateway-side codec helper that converts `ulaw_8000` to PCM16 before
  forwarding audio to the worker
- Keep the WebSocket handshake whitelist aligned with the published audio format set

**Best solution**

Do not ship a temporary "reject `ulaw_8000`" phase. Correct decoding is gateway-local
and should land as a direct parity fix.

---

## Phase 2: Cross-Service Parity (Weeks 4-7)

These items cross the gateway boundary into auth, orchestrator, export, or engine code.

### 2.1: Adapt the existing auth service to the official single-use token contract

**Current problem**

Dalston already has ephemeral session tokens, but the public ElevenLabs contract uses:

- the documented single-use token endpoint
- typed tokens for specific realtime flows
- `?token=` on the realtime WebSocket
- consume-on-first-use semantics

**Remedy**

- Expose the current ElevenLabs token issuance path on Dalston
- Reuse the existing auth token service rather than introducing a second token store
- Extend it with token type, single-use consumption, and short TTL semantics
- Accept `?token=` in WebSocket auth on the ElevenLabs route

**Best solution**

Add the official ElevenLabs-compatible endpoint and make it an adapter over the existing
token infrastructure. Do not add a Dalston-only `/v1/speech-to-text/realtime/token`
contract if the public ElevenLabs SDK expects `/v1/single-use-token/{token_type}`.

### 2.2: Reuse the existing webhook platform for ElevenLabs async delivery

**Current problem**

Dalston already has webhook endpoint CRUD and a delivery worker, but the ElevenLabs route
does not wire `webhook=true`, `webhook_id`, or `webhook_metadata` into that subsystem.

**Remedy**

- Validate `webhook=true`, `webhook_id`, and `webhook_metadata` on submission
- Route delivery through the existing webhook endpoint registry and delivery worker
- Emit the exact ElevenLabs transcript-complete / transcript-failed payloads
- Include `request_id` and metadata in the webhook body where the public contract
  requires them

**Best solution**

Treat this as an adapter problem, not a greenfield subsystem. The missing work is
ElevenLabs STT semantics on top of Dalston's existing durable webhook platform.

### 2.3: Respect model selection and control parameters end-to-end

**Current problem**

Dalston currently accepts several documented controls and either ignores them or silently
substitutes defaults:

- batch `model_id`
- realtime `model_id`
- batch `temperature`
- batch `seed`
- realtime `previous_text`
- realtime VAD tuning parameters

**Remedy**

- Route batch and realtime model IDs through separate capability rows and mappings
- Forward `temperature` and `seed` end-to-end
- Forward `previous_text` as the worker's initial prompt/context hint
- Forward VAD tuning parameters through the realtime worker stack

**Best solution**

Never silently substitute `settings.default_model` for a requested public model ID. If a
docs-backed model is not available in a deployment, reject it explicitly.

### 2.4: Implement the missing ElevenLabs batch enrichment surface

**Current problem**

The current ElevenLabs-compatible batch route omits major pieces of the documented
surface:

- `entity_detection`
- `entities`
- inline `additional_formats`

**Remedy**

- Extend the batch pipeline and merge layer so `entity_detection` can produce `entities`
- Reuse the existing export service to return `additional_formats` inline on the
  ElevenLabs response model

**Best solution**

Prioritize `entity_detection` and `entities` before chasing lower-signal platform extras.
This is a first-class public contract gap, not a nice-to-have.

### 2.5: Fix the word schema end-to-end

**Current problem**

Dalston's current `words[]` output is shaped around older assumptions:

- `type` is hardcoded to `word`
- `logprob` is missing
- `characters` are missing
- realtime and batch formatting are inconsistent

**Remedy**

- Thread `logprob` from engine output to transcript storage and API formatting
- Thread character-level alignment data where the engine provides it
- Build `type` values from actual lexical/token boundaries so `spacing` is represented
  correctly

**Best solution**

Do not synthesize `spacing` purely from silence gaps. Space tokens are textual structure,
not just pauses. Build one shared formatter for batch and realtime word payloads from the
actual transcript/token data.

---

## Phase 3: New Capability and Edge-Contract Work (Weeks 8-11)

These items are real parity work, but they are either larger features or lower-priority
than the gateway and integration fixes above.

### 3.1: Enforce the public size ceilings early

ElevenLabs documents 3 GB direct uploads and 2 GB `cloud_storage_url` inputs. Dalston
should enforce those ceilings at the gateway using early `Content-Length` checks plus
streaming byte counters, reusing the existing URL download service where possible.

### 3.2: Add `use_multi_channel` and the multichannel response shape

This requires real pipeline fan-out and a different response model. It is a valid public
gap, but it should follow the higher-value correctness work above.

### 3.3: Expose only the official concurrency headers if we can compute them exactly

If we surface rate-limit information, it should use the official ElevenLabs header names
described in the current docs, such as `current-concurrent-requests` and
`maximum-concurrent-requests`. Do not add custom `x-ratelimit-*` headers under the guise
of ElevenLabs parity.

---

## Tests

### Required Integration Coverage

- Capability-table validation for batch and realtime
- Exact response-shape fixtures for async acknowledgement, completed transcript, and
  transcript polling behavior
- Keyterm validation including the <=5 word rule
- `request_id` propagation
- `enable_logging` request-location validation
- DELETE transcript alias behavior
- Realtime `commit_strategy`, token auth, and `ulaw_8000` handling
- Webhook payload-shape validation at the adapter boundary

### Required SDK Coverage

- A pinned-version Python SDK contract suite under `tests/integration/`
- `ElevenLabs(base_url=...)` batch coverage for:
  - sync convert
  - async convert
  - get/delete
  - validation failures for unsupported model/field combinations
  - `additional_formats` where supported
  - `entity_detection` where supported
- Single-use token coverage using the official SDK helper if exposed, otherwise a raw
  HTTP fixture captured in Phase 0
- Realtime connection/setup coverage driven by SDK traces and the frozen Phase 0 event
  fixtures where the SDK exposes a stable helper
- A smaller live-stack smoke test in `tests/e2e/test_elevenlabs_sdk.py`

### CI Policy

- Pin the main CI compatibility gate to one known `elevenlabs` version
- Optionally run a non-blocking canary job against the latest `elevenlabs` release to
  catch upstream drift early

### Preparation Docs

- [PARITY_GAPS.md](../../specs/elevenlabs/PARITY_GAPS.md)
- [M62 Task 62.1: ElevenLabs SDK Contract Tests](../impl/M62-62.1-elevenlabs-sdk-contract-tests.md)

---

## Files Expected To Change

| File | Phase | Change |
|------|-------|--------|
| `pyproject.toml` | 0 | Add pinned `elevenlabs` test/dev dependency for the contract suite |
| `dalston/gateway/api/v1/speech_to_text.py` | 0, 1, 2 | Capability-table validation, exact transcript polling/delete behavior, full batch field inventory, `request_id`, inline `additional_formats`, and `entities` |
| `dalston/gateway/api/v1/realtime.py` | 0, 1, 2 | Capability-driven validation, docs-backed model handling, `commit_strategy`, `ulaw_8000` decoding, exact session/error payloads, and realtime control forwarding |
| `dalston/gateway/api/auth.py` | 0, 2 | ElevenLabs-compatible single-use token issuance path over the existing auth service |
| `dalston/gateway/middleware/auth.py` | 1, 2 | `?token=` support, single-use token consumption, and compatibility auth-path handling |
| `dalston/gateway/services/auth.py` | 2 | Token type, single-use semantics, and short-TTL behavior for the existing token system |
| `dalston/gateway/services/ingestion.py` | 3 | Early 3 GB upload ceiling enforcement |
| `dalston/gateway/services/audio_url.py` | 3 | Early 2 GB `cloud_storage_url` ceiling enforcement, likely by extending existing guards |
| `dalston/gateway/services/export.py` | 2 | Reuse existing export rendering for inline `additional_formats` |
| `dalston/common/pipeline_types.py` | 2 | Enriched transcript/token fields such as `logprob`, `entities`, and exact word payload support |
| `engines/stt-merge/final-merger/engine.py` | 2 | Preserve transcript data required for `logprob`, `characters`, `entities`, and exact word formatting |
| `dalston/realtime_sdk/session.py` | 2 | Apply forwarded realtime settings such as prompt/context and VAD tuning where supported |
| `dalston/realtime_sdk/vad.py` | 2 | VAD tuning plumbed from the public ElevenLabs realtime surface |
| `dalston/orchestrator/distributed_main.py` | 2 | Adapt completed/failed job events into exact ElevenLabs webhook semantics |
| `dalston/orchestrator/delivery.py` | 2 | Deliver exact ElevenLabs-compatible webhook payloads through the existing durable worker |
| `tests/integration/elevenlabs_fixtures/*` | 0 | Locked docs/SDK/raw-HTTP compatibility fixtures |
| `tests/integration/test_elevenlabs_api.py` | 0, 1, 2 | Narrow route-level assertions and remove stale parity assumptions |
| `tests/integration/test_elevenlabs_sdk_contract.py` | 0, 1, 2, 3 | Pinned official Python SDK compatibility suite |
| `tests/integration/test_elevenlabs_realtime_api.py` | 0, 1, 2 | Focused realtime contract coverage if the generic realtime tests are too diffuse |
| `tests/e2e/test_elevenlabs_sdk.py` | 0, 1, 2, 3 | Narrow live-stack smoke test using the official Python SDK |

---

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| ElevenLabs docs continue to drift during implementation | Freeze Phase 0 fixtures, capability rows, and schemas with an explicit March 8, 2026 date stamp |
| SDK releases drift faster than the docs | Pin one `elevenlabs` version in CI and keep an optional latest-version canary |
| Existing mock-heavy tests continue asserting stale behavior | Narrow `test_elevenlabs_api.py` to route-level concerns and make the SDK suite the primary contract gate |
| Gateway starts claiming parity with approximate transcript/token shapes | Reject unsupported combinations until exact `words[]`, `entities`, and webhook shapes are implemented |
| The SDK does not expose every public route or realtime helper cleanly | Use frozen raw HTTP/WebSocket traces for the non-SDK-visible runtime edges |
| Token flow semantics diverge between docs, SDK, and existing Dalston tokens | Freeze the token contract in Phase 0 and adapt the existing token system to that contract instead of adding a second one |
| Existing webhook infrastructure does not match ElevenLabs payload semantics exactly | Treat webhook work as an adapter layer over the current subsystem and lock payload fixtures before implementation |
| `ulaw_8000` decoding differs by Python/runtime support | Keep codec handling isolated behind a small helper and pin test coverage for the supported runtime |

---

## Checkpoint

### Phase 0

- [ ] Pinned `elevenlabs-python` version chosen for the compatibility gate
- [ ] SDK and HTTP traces captured for batch, get/delete, token issuance, and realtime
- [ ] `ELEVENLABS_STT_CAPABILITIES` defined and used as the source of truth
- [ ] Canonical transcript, webhook, realtime, and token schemas frozen
- [ ] SDK contract suite skeleton checked in with locked fixtures

### Phase 1

- [ ] Batch and realtime validation come from the capability table
- [ ] GET no longer returns a Dalston-only processing object
- [ ] DELETE transcript alias reuses the native deletion path
- [ ] `request_id` is populated in async responses
- [ ] Keyterm <=5 word validation is enforced
- [ ] Batch `enable_logging` is accepted on the query string
- [ ] `commit_strategy` defaults to `manual`
- [ ] `include_language_detection` is wired
- [ ] Realtime error messages use the ElevenLabs vocabulary
- [ ] `ulaw_8000` is decoded correctly before forwarding
- [ ] SDK contract tests cover the Phase 1 public surface and replace stale assumptions

### Phase 2

- [ ] Official single-use token endpoint implemented over the existing auth service
- [ ] WebSocket auth accepts `?token=` and consumes tokens once
- [ ] `webhook=true`, `webhook_id`, and `webhook_metadata` are wired through the existing webhook platform
- [ ] Batch and realtime `model_id` are routed or rejected explicitly
- [ ] `temperature`, `seed`, `previous_text`, and VAD tuning are forwarded end-to-end
- [ ] `entity_detection` and `entities` are implemented
- [ ] `additional_formats` are returned inline
- [ ] `logprob`, `characters`, and correct `words[].type` values are emitted
- [ ] SDK contract tests cover token, webhook, and batch enrichment semantics

### Phase 3

- [ ] 3 GB / 2 GB size ceilings are enforced before wasteful ingestion
- [ ] `use_multi_channel` is supported with the correct response model
- [ ] Official concurrency headers are returned only if computed exactly
- [ ] Live-stack SDK smoke coverage is stable against the supported ElevenLabs surface

---

## What We Are Not Closing

| Item | Reason |
|---|---|
| `Idempotency-Key` support as a parity requirement | Useful platform work, but not part of the published ElevenLabs STT contract |
| Custom `x-ratelimit-*` headers | Not the official ElevenLabs contract; use official concurrency headers or nothing |
| A temporary reject-first phase for `ulaw_8000` | Official docs already treat it as supported input |
| Silence-gap-only `spacing` synthesis | Too approximate to claim parity |
| A second webhook subsystem just for ElevenLabs | Dalston already has durable webhook infrastructure |
| A second token service or a Dalston-only realtime-token endpoint | Dalston already has token infrastructure; the public contract should be adapted onto it |
| `audio_event` word tokens without a real detector | Requires genuine new audio-event capability, not response formatting |
| Self-hosted zero-retention semantics for `enable_logging` | We can accept the flag for compatibility, but retention remains operator-controlled |

**Previous milestone**: [M61 OpenAI API Parity](M61-openai-api-parity.md)
**Next milestone**: TBD
