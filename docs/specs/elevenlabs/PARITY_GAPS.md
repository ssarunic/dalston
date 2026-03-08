# ElevenLabs STT API - Parity Gap Analysis

**Date**: 2026-03-08
**Scope**: `POST /v1/speech-to-text`, `GET /v1/speech-to-text/transcripts/{id}`, `DELETE /v1/speech-to-text/transcripts/{id}`, `WS /v1/speech-to-text/realtime`, `POST /v1/single-use-token/{token_type}`
**Companion milestone**: [M62: ElevenLabs Speech-to-Text API Parity](../../plan/milestones/M62-elevenlabs-api-parity.md)

---

## Purpose

This document is the analysis companion to
[M62: ElevenLabs Speech-to-Text API Parity](../../plan/milestones/M62-elevenlabs-api-parity.md).

- `PARITY_GAPS.md` answers: "What is wrong or missing, and what is the best remedy?"
- `M62` answers: "What should we implement, in what order, and with what constraints?"

Both documents are intentionally locked to the public ElevenLabs STT surface as of
**March 8, 2026**, using the official API reference pages and the public
[`elevenlabs-python`](https://github.com/elevenlabs/elevenlabs-python) SDK as the
tie-breaker when examples drift.

For ambiguous cases, this analysis treats the official `elevenlabs-python` SDK as the
primary consumer contract for request/response behavior, with raw HTTP/WebSocket traces
used to settle runtime details the SDK does not encode.

---

## Reading Guide

Each gap is classified on two axes:

| Feasibility | Meaning |
|---|---|
| `CURRENT` | Closable with gateway or adapter work only |
| `CROSS-SERVICE` | Requires auth, orchestrator, export, worker, or pipeline changes |
| `CONTRACT-LOCK-FIRST` | Do not implement until docs-backed and SDK-backed fixtures are frozen |
| `NEW-CAPABILITY` | Requires a genuinely new feature rather than cleanup or adapter work |

| Priority | Meaning |
|---|---|
| P0 | Current behavior is wrong for supported ElevenLabs callers |
| P1 | Required for parity of the supported public surface |
| P2 | Valid parity gap, but can follow once the contract is locked |
| P3 | Legitimate future work, not required for the first exact-compatible slice |

---

## Executive Summary

The core issue is no longer "a few missing fields". Dalston's ElevenLabs compatibility
layer is still shaped around older M08-era assumptions about the public contract.

The highest-value conclusions are:

1. **The official Python SDK must be treated as an executable compatibility contract**
   The repo currently has route-level tests, but no pinned SDK contract suite that can
   catch subtle request serialization, parsed response, auth, and realtime setup drift.

2. **The compatibility contract must be frozen from current docs plus SDK traces**
   Batch and realtime model IDs, auth, and request field locations can no longer be
   inferred from old milestone assumptions.

3. **Existing Dalston subsystems should be adapted, not duplicated**
   Delete, token issuance, webhook endpoint CRUD, and webhook delivery already exist.
   The parity work is to map the ElevenLabs contract onto them.

4. **`entity_detection` and `entities` are more important than custom platform extras**
   These are first-class public STT features today, while items such as `Idempotency-Key`
   and custom `x-ratelimit-*` headers are not core ElevenLabs parity requirements.

5. **`ulaw_8000` must be decoded, not rejected**
   The current docs treat it as supported realtime input. A reject-first plan would ship
   deliberate incompatibility.

6. **Word/token parity needs an exact formatter, not a silence-gap heuristic**
   `spacing` is textual structure, not simply silence. `logprob`, `characters`, and
   `type` should be built from actual transcript/token data.

---

## Cross-Document Contract

This document intentionally matches the structure and decisions in
[M62: ElevenLabs Speech-to-Text API Parity](../../plan/milestones/M62-elevenlabs-api-parity.md).

If one of these documents changes, the other should be reviewed in the same PR.

The contract hierarchy is:

- Public ElevenLabs docs for the intended product surface
- Pinned `elevenlabs-python` behavior for client-facing request/response semantics
- Raw HTTP/WebSocket traces for runtime semantics the SDK does not fully specify

---

## Part 1: Contract Drift

### C-0 - The parity target is not executable without a real SDK contract suite

**Priority**: P0
**Feasibility**: `CURRENT`

The repo currently has route-level integration tests in
[test_elevenlabs_api.py](../../tests/integration/test_elevenlabs_api.py), but no pinned
official SDK contract suite. That matters because the current test layer does not
exercise the parts of compatibility that usually break first:

- the exact multipart fields serialized by the official SDK
- parsed response object shapes from the official client
- single-use token creation and `?token=` auth through the client-facing surface
- SDK-visible polling behavior for `get(...)`
- realtime connection setup and commit flow where the SDK exposes it

The current route-level tests also already encode stale assumptions such as:

- arbitrary `model_id` acceptance
- a custom `200 {"status":"processing"}` GET shape

Those tests are useful for narrow handler branches, but they are not enough to act as the
primary consumer contract.

**Conclusion**

Add a pinned-version SDK contract suite under `tests/integration/` and keep a smaller
live-stack SDK smoke path under `tests/e2e/`.

### C-1 - Batch and realtime validation is still anchored to M08-era assumptions

**Priority**: P0
**Feasibility**: `CONTRACT-LOCK-FIRST`

Current batch and realtime handling in
[speech_to_text.py](../../dalston/gateway/api/v1/speech_to_text.py) and
[realtime.py](../../dalston/gateway/api/v1/realtime.py) still assumes an older model and
parameter surface:

- batch `model_id` is accepted but silently replaced with `settings.default_model`
- realtime still defaults `model_id` to `scribe_v1`
- batch and realtime model support are not separated
- field support is spread across handlers instead of one capability table

This is the root cause of several downstream parity bugs.

**Conclusion**

The first fix is not another field-by-field patch. The first fix is a docs-backed and
SDK-backed `ELEVENLABS_STT_CAPABILITIES` table with separate batch and realtime rows.

---

### C-2 - Request field inventory and field locations have drifted

**Priority**: P0
**Feasibility**: `CONTRACT-LOCK-FIRST`

The current ElevenLabs public surface includes request fields and field locations that
Dalston does not model correctly today:

- batch `enable_logging` is a query parameter, not a multipart-only concern
- batch request fields include `entity_detection`, `file_format`, `webhook_id`, and
  `webhook_metadata`
- realtime browser auth uses `?token=` and the public single-use token flow

This is not just "missing parameters". Query-vs-form placement and auth shape are part of
the public contract.

**Conclusion**

Validation must be location-aware as well as value-aware. A field in the wrong place is a
parity bug even if the value is familiar.

---

### C-3 - Several proposed "new subsystems" already exist in Dalston

**Priority**: P1
**Feasibility**: `CURRENT`

The earlier milestone/report direction overstated how much infrastructure is missing.
Dalston already has:

- native transcript deletion in
  [transcription.py](../../dalston/gateway/api/v1/transcription.py)
- ephemeral token issuance in
  [auth.py](../../dalston/gateway/api/auth.py)
- WebSocket auth middleware in
  [auth.py](../../dalston/gateway/middleware/auth.py)
- webhook endpoint CRUD in
  [webhooks.py](../../dalston/gateway/api/v1/webhooks.py) and
  [webhook_endpoints.py](../../dalston/gateway/services/webhook_endpoints.py)
- durable webhook delivery in
  [delivery.py](../../dalston/orchestrator/delivery.py) and
  [distributed_main.py](../../dalston/orchestrator/distributed_main.py)

**Important correction**

The parity work is to adapt these subsystems to the ElevenLabs surface. Building parallel
"ElevenLabs-only" delete, token, or webhook subsystems would add complexity without
solving a real platform gap.

---

## Part 2: Batch Endpoint Gaps

### G-1 - GET transcript returns a Dalston-only processing schema, and DELETE alias is missing

**Priority**: P0
**Feasibility**: `CURRENT`

The current ElevenLabs-compatible route in
[speech_to_text.py](../../dalston/gateway/api/v1/speech_to_text.py)
returns a custom processing response while a job is still running. That is not part of
the public ElevenLabs contract. The DELETE alias is also missing.

**Important correction**

The right fix is not to add a new soft-delete subsystem or a new artifact-deletion queue.
Dalston already has those concerns in the native transcription path.

**Best remedy**

- Remove the custom processing schema from the ElevenLabs route
- Return a transcript only when it is actually materialized
- While pending, return ElevenLabs-compatible `404` plus `Retry-After`
- Add the ElevenLabs DELETE alias as a thin adapter over the existing native deletion path

Exact terminal failure polling behavior should be frozen from SDK/API traces before we
claim anything more specific.

---

### G-2 - Batch `model_id` is silently ignored

**Priority**: P0
**Feasibility**: `CROSS-SERVICE`

Current batch handling in
[speech_to_text.py](../../dalston/gateway/api/v1/speech_to_text.py)
accepts `model_id` and then routes all work through `settings.default_model`.

That is a silent behavior change visible to any client that chooses models for
latency/quality tradeoffs.

**Important correction**

The fix is not to keep accepting arbitrary model IDs and silently substituting a default.
The fix is explicit model routing or explicit rejection.

**Best remedy**

Create a batch-specific capability table and deployment mapping. If the requested public
model is not available, return a clear compatibility error instead of silently falling
back.

---

### G-3 - `request_id` is always null in async responses

**Priority**: P1
**Feasibility**: `CURRENT`

Dalston already generates and propagates request IDs, but the ElevenLabs async response
does not populate `request_id`.

**Best remedy**

Populate it from the existing correlation middleware state. This is an easy correctness
fix and should land early.

---

### G-4 - Keyterm validation misses the published <=5 word rule

**Priority**: P1
**Feasibility**: `CURRENT`

Current validation checks count and character length, but not the published per-term word
limit. That creates avoidable acceptance drift for both batch and realtime callers.

**Best remedy**

Enforce the word-count limit in both routes and return a public-shape validation error.

---

### G-5 - The batch request surface is incomplete and partly in the wrong place

**Priority**: P0
**Feasibility**: `CROSS-SERVICE`

The current ElevenLabs-compatible batch handler does not fully model the current public
request surface. Notable examples:

- `enable_logging` is not modeled as the current query parameter contract
- `entity_detection` is missing
- `file_format` is missing
- `webhook_id` and `webhook_metadata` are missing or not consistently validated

**Important correction**

The earlier milestone treated `enable_logging` mainly as a form-acceptance issue. The
real issue is the public request contract, not just suppressing a `422`.

**Best remedy**

Parse and validate the full docs-backed request surface, using the right locations for
each field. Any documented field that is still unsupported end-to-end should be rejected
explicitly rather than accepted and discarded.

---

### G-6 - `additional_formats` is not returned inline

**Priority**: P1
**Feasibility**: `CROSS-SERVICE`

Dalston already has export functionality, but the ElevenLabs-compatible response does not
embed `additional_formats` inline the way the public contract expects.

**Best remedy**

Reuse the existing export service and expose it through the ElevenLabs response model.
This is an adapter problem, not a net-new export feature.

---

### G-7 - `entity_detection` and `entities` are missing from the public batch route

**Priority**: P1
**Feasibility**: `CROSS-SERVICE`

This is one of the most important omissions in the current milestone/report direction.
The public batch surface includes request-time entity detection and response-time entity
annotations, while Dalston's ElevenLabs route exposes neither.

**Important correction**

This gap is more important than lower-signal extras like custom rate-limit headers or
idempotency middleware if the goal is ElevenLabs STT parity.

**Best remedy**

Extend the existing annotation/PII path so the batch pipeline can emit `entities` in the
public ElevenLabs shape, keyed by the requested `entity_detection` configuration.

---

### G-8 - `temperature` and `seed` are still silent control-parameter gaps

**Priority**: P2
**Feasibility**: `CROSS-SERVICE`

These documented controls are not currently forwarded end-to-end on the ElevenLabs route.
This is a real gap, but it sits behind the higher-value contract and adapter work above.

**Best remedy**

Forward them explicitly, including `temperature=0`, and reject them when the active
engine cannot honor them.

---

## Part 3: Realtime Endpoint Gaps

### G-9 - `commit_strategy` still defaults to the wrong value

**Priority**: P0
**Feasibility**: `CURRENT`

Current realtime handling in
[realtime.py](../../dalston/gateway/api/v1/realtime.py)
defaults `commit_strategy` to `vad`. The current ElevenLabs public contract defaults it
to `manual`.

**Best remedy**

Change the default immediately. This is a straightforward gateway bug.

---

### G-10 - The public single-use token contract is missing

**Priority**: P0
**Feasibility**: `CROSS-SERVICE`

Browser-safe ElevenLabs realtime auth now uses:

- the public single-use token endpoint
- typed tokens
- `?token=` on the WebSocket
- single-use consumption semantics

Dalston currently exposes reusable ephemeral tokens and WebSocket auth centered on
`api_key=`.

**Important correction**

The right fix is not to add `/v1/speech-to-text/realtime/token`. The current public
surface uses `/v1/single-use-token/{token_type}`.

**Best remedy**

Adapt Dalston's existing token service to the official ElevenLabs path and behavior:
short TTL, token type, consume on first successful use, and `?token=` on the realtime
route.

---

### G-11 - `ulaw_8000` is accepted then corrupted

**Priority**: P0
**Feasibility**: `CURRENT`

Current realtime chunk handling in
[realtime.py](../../dalston/gateway/api/v1/realtime.py)
base64-decodes input and forwards it as if every format were PCM16. That corrupts
`ulaw_8000` sessions.

**Important correction**

The earlier reject-first proposal is the wrong parity move. The public contract already
treats `ulaw_8000` as supported.

**Best remedy**

Decode mu-law to PCM in the gateway before forwarding audio to the worker. Keep this in a
small codec helper so the runtime dependency can be swapped cleanly if needed.

---

### G-12 - `include_language_detection`, `previous_text`, and VAD tuning are dropped

**Priority**: P1
**Feasibility**: `CROSS-SERVICE`

The realtime public surface now includes several controls that Dalston currently accepts
partially or ignores:

- `include_language_detection`
- `previous_text`
- VAD tuning parameters such as silence thresholds

**Best remedy**

Normalize and validate these fields in the gateway, then forward them end-to-end through
the realtime worker stack. Unsupported combinations should be rejected explicitly rather
than echoed as if they were active.

---

### G-13 - `session_started` and realtime error messages are stale

**Priority**: P1
**Feasibility**: `CURRENT`

Dalston's `session_started` echo is incomplete, and realtime failures are collapsed to a
generic `error` type rather than the ElevenLabs message vocabulary.

**Important correction**

We should not "complete the echo" by reflecting fields that are still ignored. That would
misrepresent the effective config.

**Best remedy**

Echo only normalized, actually supported config and map gateway/worker failures to the
official error types the current SDK/docs describe.

---

### G-14 - The realtime and batch word payloads are still shaped around older assumptions

**Priority**: P1
**Feasibility**: `CROSS-SERVICE`

Current formatting omits or hardcodes fields that matter in the public contract:

- `logprob`
- `characters`
- `type`, including correct `spacing`

**Important correction**

The earlier spacing remedy proposed a silence-gap heuristic. That is too approximate to
claim public-shape parity.

**Best remedy**

Build one shared token/word formatter from transcript text plus per-token alignment data,
then use it consistently in batch and realtime output.

---

## Part 4: Integration and New Capability Gaps

### G-15 - `webhook=true` is not wired to Dalston's existing webhook subsystem

**Priority**: P1
**Feasibility**: `CROSS-SERVICE`

Dalston already has webhook endpoint registration and durable delivery, but the ElevenLabs
route does not map `webhook=true`, `webhook_id`, or `webhook_metadata` into that flow.

**Important correction**

This is not a "build a webhook platform" problem. The webhook platform already exists.

**Best remedy**

Validate and persist the ElevenLabs webhook fields, then emit the exact ElevenLabs STT
delivery payloads through the existing delivery worker.

---

### G-16 - Early upload ceilings and exact concurrency headers are still unfinished

**Priority**: P2
**Feasibility**: `CURRENT`

The public docs document:

- 3 GB direct uploads
- 2 GB `cloud_storage_url` inputs
- official concurrency headers

Dalston does not yet enforce the size ceilings early enough, and it should not expose
custom `x-ratelimit-*` headers as if those were the public contract.

**Best remedy**

Add early size enforcement, and only emit the official concurrency header names if
Dalston can compute them exactly.

---

### G-17 - `use_multi_channel` is a real public gap, but it is genuine new capability work

**Priority**: P2
**Feasibility**: `NEW-CAPABILITY`

This requires pipeline fan-out and a different response model. It is a valid public gap,
but it should follow the contract and correctness work above.

---

### G-18 - `audio_event` tokens remain a genuine feature gap

**Priority**: P3
**Feasibility**: `NEW-CAPABILITY`

Producing `audio_event` tokens is not just response formatting. It requires an actual
event-detection capability. This should stay out of the first exact-compatible slice.

---

## Part 5: De-Prioritized or Non-Parity Work

### N-1 - `Idempotency-Key` is not the right milestone driver

`Idempotency-Key` support may still be worth building as a general platform feature, but
it should not displace real ElevenLabs contract gaps such as `entity_detection`,
single-use tokens, or exact webhook behavior.

---

### N-2 - Custom `x-ratelimit-*` headers are not ElevenLabs parity

If we expose rate-limit information on the ElevenLabs route, it should use the current
official ElevenLabs header names. A custom `x-ratelimit-*` contract would be a Dalston
extension, not parity.

---

## Recommended Order

1. Freeze the contract from docs + SDK traces and define the pinned SDK contract suite.
2. Fix gateway-visible correctness bugs: GET/DELETE semantics, `commit_strategy`,
   `request_id`, keyterm validation, and `ulaw_8000`.
3. Adapt existing auth and webhook subsystems to the public ElevenLabs token and webhook
   contracts.
4. Route model IDs and control parameters end-to-end without silent substitution.
5. Finish batch enrichment and exact word/token formatting.
6. Tackle multi-channel and other genuine new-capability work last.
