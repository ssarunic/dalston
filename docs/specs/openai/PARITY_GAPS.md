# OpenAI STT API — Parity Gap Analysis

**Date**: 2026-03-08
**Scope**: `POST /v1/audio/transcriptions`, `POST /v1/audio/translations`, `POST /v1/realtime/transcription_sessions`, `WS /v1/realtime`
**Companion milestone**: [M61: OpenAI Speech-to-Text API Parity](../../plan/milestones/M61-openai-api-parity.md)

---

## Purpose

This document is the analysis companion to
[M61: OpenAI Speech-to-Text API Parity](../../plan/milestones/M61-openai-api-parity.md).

- `PARITY_GAPS.md` answers: "What is currently wrong or missing, and why?"
- `M61` answers: "What should we implement, in what order, and with what constraints?"

Both documents are intentionally locked to the public OpenAI STT surface as of
**March 8, 2026**.

For ambiguous cases, this analysis treats the official `openai-python` SDK as the
primary consumer-contract for request/response behavior, with real OpenAI traces used
to settle engine_id details the SDK does not encode.

---

## Reading Guide

Each gap is classified on two axes:

| Feasibility | Meaning |
|-------------|---------|
| ✅ Current | Closable with gateway or adapter code only |
| 🔧 Cross-service | Requires worker, pipeline, engine, or schema changes |
| 🧪 Contract lock first | Do not implement until SDK traces and exact docs-backed schemas are frozen |
| 🧱 New subsystem | Requires a genuinely new subsystem or capability |

| Priority | Meaning |
|----------|---------|
| P0 | Current behavior is wrong for supported OpenAI callers |
| P1 | Required for parity of the supported surface |
| P2 | Valid parity gap, but can follow once the contract is locked |
| P3 | Legitimate future parity work, not required for the first exact-compatible slice |

---

## Executive Summary

The main problem is no longer "a few missing fields". The real issue is that Dalston's
OpenAI compatibility layer is still shaped around an older OpenAI STT surface.

The highest-value conclusions are:

1. **The official Python SDK must be treated as an executable compatibility contract**
   The repo currently has only a thin SDK smoke test, which is not enough to catch
   subtle request, response, header, and realtime setup mismatches.

2. **Model and capability validation must become table-driven**
   The current regex-based approach is too loose and cannot model per-model constraints.

3. **Response-shape parity must be exact**
   Old assumptions such as universal `usage.audio_seconds`, a top-level `model` field,
   or older diarized response shapes must not be carried forward.

4. **`prompt` must remain free text**
   Converting it into gateway-level hotwords is the wrong abstraction.

5. **Realtime parity requires exact item-graph behavior, 24 kHz correctness, and SDK-visible session setup**
   The `pcm16` bug and the missing item events are the biggest realtime correctness issues.

6. **Some gaps are real, but they are subsystem work rather than cleanup**
   SSE streaming, speaker references, and realtime denoise belong in a later track.

---

## Cross-Document Contract

This document intentionally matches the structure and decisions in
[M61: OpenAI Speech-to-Text API Parity](../../plan/milestones/M61-openai-api-parity.md).

If one of these documents changes, the other should be reviewed in the same PR.

The contract hierarchy is:

- Public OpenAI docs for the intended product surface
- Pinned `openai-python` behavior for client-facing request/response semantics
- Real OpenAI traces for engine_id semantics the SDK does not fully specify

---

## Part 1: Contract Drift

### C-0 · The parity target is not executable without a real SDK contract suite

**Priority**: P0
**Feasibility**: ✅ Current

The repo currently contains only a thin SDK smoke test in
[tests/e2e/test_openai_sdk.py](../../tests/e2e/test_openai_sdk.py).
That does not exercise the parts of compatibility that usually break first:

- the exact multipart fields serialized by the official SDK
- parsed response unions and object shapes
- raw response headers via `with_raw_response`
- batch streaming event parsing
- realtime transcription-session creation

Because the public OpenAI docs are ambiguous in some areas, especially realtime, parity
cannot be treated as complete until the official Python SDK is part of the required test
contract.

**Conclusion**

Add a pinned-version SDK contract suite under `tests/integration/` and keep the current
e2e SDK test as a smaller live-stack smoke path.

### C-1 · Model validation is regex-driven instead of capability-driven

**Priority**: P0
**Feasibility**: 🧪 Contract lock first

Current code in [openai_audio.py](../../dalston/gateway/api/v1/openai_audio.py)
detects OpenAI models using permissive patterns. That allows undocumented names and
cannot express endpoint-specific or feature-specific support.

This creates three concrete failures:

- unsupported models can be accepted
- supported models can be misvalidated for the wrong endpoint
- parameters and response formats cannot be gated by model

**Conclusion**

This is the root cause of many downstream parity mismatches. It must be replaced with a
single docs-backed capability table, as specified in
[M61 Phase 0](../../plan/milestones/M61-openai-api-parity.md#phase-0-contract-lock-week-1).

---

### C-2 · Translation support is frozen to an older assumption

**Priority**: P1
**Feasibility**: 🧪 Contract lock first

Current translation validation in
[openai_translation.py](../../dalston/gateway/api/v1/openai_translation.py)
hardcodes `whisper-1` and maintains its own response-format rules.

That means translation behavior is not aligned with the same docs source as the
transcription endpoint.

**Conclusion**

Translation must be brought under the same capability table as transcription.

---

### C-3 · Realtime model surface remains narrower than OpenAI's full matrix

**Priority**: P2
**Feasibility**: 🔧 Cross-service

Current realtime support in
[openai_realtime.py](../../dalston/gateway/api/v1/openai_realtime.py)
still exposes a narrower model/engine_id surface than OpenAI's full realtime
matrix.

Request-shape normalization work is now in place:

- both wrapped and flat session-update payloads are accepted and normalized
- nested invalid `{"session":{"session":...}}` payloads fail fast
- realtime session-create REST shape is handled consistently with websocket
  normalization

**Conclusion**

Realtime request-shape drift is no longer the blocker. Remaining work is model
surface breadth and capability parity.

---

## Part 2: Batch Endpoint Gaps

### G-1 · `diarized_json` silently falls through to `json`

**Priority**: P0
**Feasibility**: ✅ Current

This is a real correctness bug in the current code. `diarized_json` is documented in the
form description but not implemented in the formatter.

However, the remedy must use the **current** OpenAI diarized schema, not older internal
assumptions.

**Important correction**

We should not assume:

- old `utterances[]` shapes
- universal `usage.audio_seconds`
- a top-level `model` field

See the implementation direction in
[M61 1.2](../../plan/milestones/M61-openai-api-parity.md#12-fix-diarized_json-using-the-exact-openai-schema).

---

### G-2 · `usage` is missing, and the old one-size-fits-all remedy is wrong

**Priority**: P0
**Feasibility**: 🧪 Contract lock first

Dalston currently omits `usage`, but the older assumption that all STT responses should
return `{"type":"audio","audio_seconds":...}` is no longer safe.

The OpenAI STT surface is now model-dependent. Some models/formats may require different
usage semantics.

**Conclusion**

The real gap is "usage is not model-aware and exact". We should not land a universal
audio-seconds response as a shortcut.

---

### G-3 · `temperature=0` is silently dropped

**Priority**: P0
**Feasibility**: ✅ Current

Current code in:

- [transcription.py](../../dalston/gateway/api/v1/transcription.py)
- [openai_translation.py](../../dalston/gateway/api/v1/openai_translation.py)

only forwards temperature when it is greater than zero.

This is a straightforward correctness bug. Explicit zero is semantically meaningful.

**Conclusion**

This remains an easy fix and should land early.

---

### G-4 · `prompt` is treated as hotwords instead of prompt text

**Priority**: P0
**Feasibility**: 🔧 Cross-service

Current behavior is inconsistent and wrong:

- batch path may pass raw prompt text into `vocabulary`
- realtime path may split prompt on commas
- engines interpret `vocabulary` as hotwords / boost terms rather than free-text context

This is not just a validation bug. It is an abstraction mismatch.

**Conclusion**

The right fix is not "canonical prompt-to-vocabulary splitting". The right fix is to add
a first-class internal prompt field and let engine adapters consume it natively where possible.

See [M61 2.1](../../plan/milestones/M61-openai-api-parity.md#21-preserve-prompt-as-a-first-class-field-end-to-end).

---

### G-5 · Prompt length is documented but not validated

**Priority**: P1
**Feasibility**: ✅ Current

The current gateway never enforces the documented prompt limit.

The older proposal of a pure character heuristic is weak but still directionally useful.
The better remedy is token-aware validation with a conservative fallback.

**Conclusion**

This is a real gap, but the implementation should be token-aware rather than character-only.

---

### G-6 · `chunking_strategy` analysis in the old doc is stale

**Priority**: P2
**Feasibility**: 🧪 Contract lock first

The older parity analysis assumed `chunking_strategy="auto"` as a bare string. That is no
longer a safe assumption.

The real gap is:

- Dalston does not parse the **current docs-backed request shape**
- Dalston does not gate support by model

**Conclusion**

Treat this as a request-schema and capability-table gap, not a string-acceptance tweak.

---

### G-7 · `known_speaker_names` is a real, feasible gap

**Priority**: P1
**Feasibility**: 🔧 Cross-service

This gap remains valid. Dalston already has the right architecture to solve it:

- gateway parses the names
- DAG carries them
- merge stage relabels speaker metadata

The only correction is that the implementation must target the exact current diarized
response shape, not an older assumed output schema.

---

### G-8 · `known_speaker_references` is real subsystem work

**Priority**: P3
**Feasibility**: 🧱 New subsystem

This remains correctly classified as requiring a new enrollment / speaker-embedding path.

No change in conclusion here, except that it should stay clearly separated from the much
easier `known_speaker_names` relabeling work.

---

### G-9 · `include=item.input_audio_transcription.logprobs` is not the same as segment quality metadata

**Priority**: P2
**Feasibility**: 🔧 Cross-service

The older analysis collapsed two things into one:

- real segment-quality metadata such as `avg_logprob`
- OpenAI's newer `include=...logprobs` transcription feature

Those are related but not equivalent.

**Conclusion**

We need to track them separately:

- one track for preserving true segment metadata in `verbose_json`
- one track for exact OpenAI `include` support

---

### G-10 · `verbose_json` quality fields are currently fake

**Priority**: P1
**Feasibility**: 🔧 Cross-service

This is a real parity issue independent of `include=...logprobs`.

Current `verbose_json` returns hardcoded sentinels for:

- `avg_logprob`
- `compression_ratio`
- `no_speech_prob`
- `tokens`

If an engine can produce real values, Dalston should preserve and return them.

**Conclusion**

This remains a valid medium-sized cross-service fix.

---

## Part 3: Translation Endpoint Gaps

### T-1 · Translation validation must be capability-driven

**Priority**: P1
**Feasibility**: 🧪 Contract lock first

The old analysis described Dalston's extra translation formats as a harmless extension.
That is not the right framing for OpenAI-compatible routes.

If the goal is exact OpenAI parity on OpenAI routes, then translation should be validated
against the same capability table and exact docs-backed formats as everything else.

**Conclusion**

This is not about whether extra formats are useful. It is about whether the OpenAI route
is exact.

---

### T-2 · `stream=true` on translation remains new subsystem work

**Priority**: P3
**Feasibility**: 🧱 New subsystem

No substantive change from the earlier analysis. This still depends on the same partial
result streaming infrastructure as transcription SSE.

---

## Part 4: Realtime Gaps

### R-0 · The SDK-visible realtime session-create endpoint is missing from the parity target

**Priority**: P1
**Feasibility**: 🧪 Contract lock first

The official Python SDK exposes
`client.beta.realtime.transcription_sessions.create(...)`.
Dalston's current parity work is centered on the WebSocket route and sparse
session-created events, which is not enough for true SDK compatibility.

**Conclusion**

`/v1/realtime/transcription_sessions` needs to be part of the frozen contract and should
share the same capability validation and session normalization as the WebSocket flow.

### R-1 · `pcm16` is wrong on the OpenAI route

**Priority**: P0
**Feasibility**: 🔧 Cross-service

This remains the highest-impact realtime correctness bug.

The correction to the old analysis is in the remedy framing:

- internal resampling is the right answer
- advertising a Dalston-only public format on the OpenAI route is not

**Conclusion**

Keep `/v1/realtime` spec-accurate and resample internally.

See [M61 3.3](../../plan/milestones/M61-openai-api-parity.md#33-fix-pcm16-to-mean-24-khz-on-the-openai-route).

---

### R-2 · Missing item-graph events on commit

**Priority**: P0
**Feasibility**: ✅ Current for event emission, 🔧 Cross-service for full correctness

The gaps remain real:

- `conversation.item.created` missing
- `input_audio_buffer.committed.previous_item_id` missing
- `speech_started.item_id` missing
- `speech_stopped.item_id` missing

But the old analysis understated the state-management issue. The problem is not just
"missing fields"; the current item cursor logic is too simplistic and can drift.

**Conclusion**

Fixing the event shapes and fixing the item-state model should be treated as one piece of work.

---

### R-3 · `turn_detection` tuning is currently discarded

**Priority**: P1
**Feasibility**: 🔧 Cross-service

This remains a valid gap.

The extra nuance is that `prefix_padding_ms` should be mapped onto the existing VAD
lookback behavior rather than a separate ad hoc buffer.

---

### R-4 · Realtime request/session shape drift

**Priority**: P3
**Feasibility**: ✅ Current

The older analysis assumed only the older `transcription_session.*` shape.

Implemented behavior now covers documented variants:

- Dalston accepts wrapped and flat request variants.
- Dalston normalizes both into one internal config.
- Dalston rejects doubly nested `session` payloads explicitly.

**Conclusion**

Request/session shape drift has been addressed. Keep regression tests for both
variants in place.

---

### R-5 · `noise_reduction` is config-only today

**Priority**: P3
**Feasibility**: 🧱 New subsystem

This remains correctly classified as a real new capability rather than a cleanup task.

One correction: we should be careful not to fake support merely by echoing fields.
Echoing `null` or unsupported config is fine if the contract requires it; claiming active
noise reduction without a DSP stage is not.

---

## Part 5: Blind Spots and Corrections

### B-1 · OpenAI rate-limit headers are missing under the wrong names

**Priority**: P1
**Feasibility**: ✅ Current

The older analysis said Dalston returned no rate-limit headers. That is not quite true:
legacy `X-RateLimit-*` headers are already emitted on some 429 paths.

The real gap is:

- OpenAI header names are not consistently emitted
- success responses do not consistently carry rate-limit headers

**Conclusion**

This is still real, but it should be fixed in the existing dependency flow rather than
via a new response middleware by default.

---

### B-2 · URL-size enforcement gap was overstated

**Priority**: P1
**Feasibility**: ✅ Current

The old analysis said large URL downloads effectively bypassed limits. In reality,
[audio_url.py](../../dalston/gateway/services/audio_url.py) already enforces:

- `Content-Length` checks
- streaming byte-count checks

The real bug is that OpenAI routes do not pass the 25 MB OpenAI ceiling into ingestion.

**Conclusion**

The downloader is mostly correct already. The OpenAI-mode limit is not threaded through.

---

### B-3 · Sync-mode scalability is not a parity requirement

**Priority**: P3
**Feasibility**: N/A

The old analysis framed synchronous OpenAI-mode waiting as tying up HTTP threads. The
current implementation in [polling.py](../../dalston/gateway/services/polling.py) is an
async polling loop, so that framing was too strong.

There may still be a performance or latency reason to replace polling with a pub/sub wakeup,
but that is not itself an OpenAI parity gap.

**Conclusion**

Track this as performance work, not parity work.

---

### B-4 · `sk-` key handling was described incorrectly

**Priority**: P1
**Feasibility**: ✅ Current

The older analysis implied that `sk-` keys were accepted. The current code does not do that;
they fail generic auth. The real gap is diagnostic quality, not silent acceptance.

**Conclusion**

Add a targeted auth diagnostic for accidental OpenAI key usage.

---

### B-5 · Binary realtime frames still deserve explicit validation work

**Priority**: P2
**Feasibility**: ✅ Current for shallow checks / 🔧 Cross-service for stronger guarantees

This gap remains valid. Raw binary frames can still bypass useful structural validation.
It is worth keeping, but it is not in the first parity slice.

---

## Recommended Order

This analysis maps directly onto
[M61: OpenAI Speech-to-Text API Parity](../../plan/milestones/M61-openai-api-parity.md):

1. **Contract lock first**
   Freeze the pinned SDK contract, real traces, model capability table, and exact
   request/response shapes.

2. **Gateway correctness second**
   Fix validation, exact response formatting, `temperature=0`, prompt-length validation,
   rate-limit headers, `sk-` diagnostics, and ingestion limit threading.

3. **Batch pipeline fidelity third**
   Preserve prompt as prompt, implement `known_speaker_names`, preserve real segment
   quality metadata, and add model-gated `include=...logprobs`.

4. **Realtime correctness fourth**
   Add the SDK-visible session-create endpoint, accept both request shapes, fix the item
   graph, fix 24 kHz `pcm16`, and forward full `turn_detection` tuning.

5. **Subsystem work later**
   SSE, speaker references, denoise DSP, and any pub/sub wakeup optimization.

---

## Summary Table

| ID | Gap | Priority | Feasibility |
|----|-----|----------|-------------|
| C-0 | No pinned SDK contract suite exists yet | P0 | ✅ Current |
| C-1 | Model validation is regex-driven | P0 | 🧪 Contract lock first |
| C-2 | Translation validation is split-brain | P1 | 🧪 Contract lock first |
| C-3 | Realtime model surface is narrower than OpenAI matrix (shape normalization fixed) | P2 | 🔧 Cross-service |
| G-1 | `diarized_json` fallback bug | P0 | ✅ Current |
| G-2 | `usage` missing and older remedy is stale | P0 | 🧪 Contract lock first |
| G-3 | `temperature=0` dropped | P0 | ✅ Current |
| G-4 | `prompt` treated as hotwords | P0 | 🔧 Cross-service |
| G-5 | Prompt length not validated | P1 | ✅ Current |
| G-6 | `chunking_strategy` analysis is stale | P2 | 🧪 Contract lock first |
| G-7 | `known_speaker_names` missing | P1 | 🔧 Cross-service |
| G-8 | `known_speaker_references` missing | P3 | 🧱 New subsystem |
| G-9 | `include=...logprobs` is a distinct gap | P2 | 🔧 Cross-service |
| G-10 | `verbose_json` quality fields are fake | P1 | 🔧 Cross-service |
| T-1 | Translation route needs exact capability-driven validation | P1 | 🧪 Contract lock first |
| T-2 | Translation SSE missing | P3 | 🧱 New subsystem |
| R-0 | SDK-visible realtime session-create route missing from parity target | P1 | 🧪 Contract lock first |
| R-1 | `pcm16` 24 kHz mismatch | P0 | 🔧 Cross-service |
| R-2 | Realtime item-graph events/state are incomplete | P0 | ✅/🔧 |
| R-3 | `turn_detection` tuning ignored | P1 | 🔧 Cross-service |
| R-4 | Realtime request/session shape drift fixed (dual-shape normalization) | P3 | ✅ Current |
| R-5 | `noise_reduction` not implemented | P3 | 🧱 New subsystem |
| B-1 | OpenAI rate-limit header names missing | P1 | ✅ Current |
| B-2 | OpenAI 25 MB limit not threaded through URL ingestion | P1 | ✅ Current |
| B-3 | Sync-mode scalability is performance work, not parity | P3 | N/A |
| B-4 | `sk-` auth diagnostics missing | P1 | ✅ Current |
| B-5 | Binary realtime frames bypass useful validation | P2 | ✅/🔧 |

---

## Maintenance Rule

When updating this document, also review:

- [M61: OpenAI Speech-to-Text API Parity](../../plan/milestones/M61-openai-api-parity.md)

When updating the milestone, also review this analysis.

When updating the pinned `openai-python` version or the SDK trace fixtures, review both
documents in the same PR.
