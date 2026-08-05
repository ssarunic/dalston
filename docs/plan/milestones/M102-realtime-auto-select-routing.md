# M102: Realtime Auto-Select Must Route to an Engine That Can Serve

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | Make "Any available" realtime routing pick a model whose engine actually has a live realtime worker, treating native streaming as a preference rather than an eligibility gate |
| **Duration**       | 1 day                                                        |
| **Dependencies**   | M43 (Dynamic Model Loading), M101 (Registry Heartbeat Fields) |
| **Deliverable**    | Corrected auto-select in `_realtime_common.py`, honest error when nothing can serve, `native_streaming` fixed for NeMo TDT/RNNT models, tests |
| **Status**         | Not Started                                                  |

## User Story

> *"As a user picking 'Any available', I want the gateway to choose a model it can actually stream with, so that I get a working session instead of 'No realtime workers available' while a perfectly good worker sits idle."*

---

## Outcomes

| Scenario | Current | After M102 |
| -------- | ------- | ---------- |
| "Any available" with a live NeMo RT worker | Routes to `vllm-asr-voxtral-mini-3b` (largest model overall), which has no RT worker → *"No realtime workers available"* | Routes to `nvidia/parakeet-tdt-0.6b-v3`; session starts |
| A non-native model is the only RT-capable one | Excluded from `rt_models`, so the fallback ignores realtime entirely and may pick anything | Selected and served through the SDK's VAD wrapper |
| No engine has a realtime worker | *"No realtime workers available. Try again later."* — accurate by accident | Same class of error, but raised deliberately with the reason |
| Native and wrapped models both available | Native flag is a hard gate that nothing passes | Native preferred, wrapped used only as second choice |

---

## Motivation

`resolve_rt_routing` gates realtime eligibility on `native_streaming`:

```python
rt_models = [m for m in downloaded_models
             if m.native_streaming and m.engine_id in live_rt_engine_ids]
...
candidates = rt_models if rt_models else list(downloaded_models)   # ← drops the RT constraint
largest = max(candidates, key=lambda m: m.size_bytes or 0)
```

That is the wrong constraint. The realtime SDK wraps non-native models with VAD segmentation — slicing the stream at pauses and transcribing each segment — so **any** engine with a live realtime worker can serve a session. Native decoding (cache-aware RNNT/TDT) gives better interactivity and partial results, but it is a *quality preference*, not a capability requirement.

Two failures follow:

1. **The gate is too strict**, so `rt_models` is usually empty.
2. **The fallback is too loose**, discarding the realtime constraint entirely and choosing from every downloaded transcribe model — including engines with no realtime worker, which guarantees the allocation fails.

### This fires on essentially every deployment

`native_streaming` is declared per model in `models/*.yaml` (default `false`). Across the whole repo **exactly one** model declares `true` — `parakeet-rnnt-0.6b` — and it is not a commonly downloaded model. So `rt_models` is empty almost always, the fallback always runs, and "Any available" has effectively never worked as designed.

### Observed 2026-08-05

With a healthy NeMo realtime worker registered (`live_rt_engine_ids = {'nemo'}`):

```
ready transcribe models:
  Systran/faster-whisper-large-v3        engine=faster-whisper  native_streaming=False   3091MB
  istupakov/parakeet-tdt-0.6b-v3-onnx    engine=onnx            native_streaming=False   3220MB
  nvidia/parakeet-tdt-0.6b-v3            engine=nemo            native_streaming=False   2509MB
  vllm-asr-voxtral-mini-3b               engine=vllm-asr        native_streaming=False  18720MB

rt_models: 0  →  fallback  →  AUTO-SELECT PICKS: vllm-asr-voxtral-mini-3b
                              has live RT worker = False  →  allocation fails
```

Selecting Parakeet explicitly worked, because that path uses the model's `engine_id` directly and never touches this logic.

### The flag is also factually wrong for NeMo TDT

`NeMoInference.supports_native_streaming_decode()` is authoritative: `STREAMING_DECODER_TYPES = {"rnnt", "tdt"}` — RNNT and TDT emit tokens frame-by-frame, CTC cannot stream. The live engine reports `supports_native_streaming: true` for `nvidia/parakeet-tdt-0.6b-v3`, while `models/parakeet-tdt-0.6b-v3.yaml:54` declares `native_streaming: false`. The YAML contradicts the engine.

Note this is a *preference* error once the routing fix lands — it costs correct ranking, not function.

---

## Steps

### 102.1: Filter on realtime workers, rank on native streaming

**Files modified:**

- `dalston/gateway/api/v1/_realtime_common.py` — the `else:` auto-select branch

**Deliverables:**

Replace the native-streaming gate with the real constraint, and demote the flag to a ranking key:

```python
# Hard constraint: the engine must have a live realtime worker. Anything
# else cannot serve the session regardless of its capabilities.
servable = [m for m in downloaded_models if m.engine_id in live_rt_engine_ids]

# Preference: native streaming (cache-aware RNNT/TDT) gives lower latency
# and real partials; non-native models are served through the SDK's VAD
# wrapper, so they are usable, just second choice.
best = max(servable, key=lambda m: (bool(m.native_streaming), m.size_bytes or 0))
```

Language filtering narrows `servable` but must never widen past it — falling back to a non-servable model is what produced the misleading error.

---

### 102.2: Fail with the actual reason

**Files modified:**

- `dalston/gateway/api/v1/_realtime_common.py`

**Deliverables:**

When no downloaded model belongs to an engine with a realtime worker, raise `ValueError` naming the cause rather than letting allocation fail later with a generic message. Follows the existing pattern for unsupported-language errors in the same function.

The current `except Exception` around the registry lookup must not swallow this — the deliberate raise has to happen outside it, or be re-raised.

---

### 102.3: Correct `native_streaming` for NeMo TDT models

**Files modified:**

- `models/parakeet-tdt-0.6b-v3.yaml`
- `models/parakeet-tdt-1.1b.yaml`

**Deliverables:**

Set `native_streaming: true`. Both are TDT decoders on the `nemo` engine, which `supports_native_streaming_decode()` reports as streaming-capable, and `parakeet-rnnt-0.6b.yaml` already declares `true` for the same reason.

Do **not** change the `istupakov/*-onnx` variants: they run on the `onnx` engine, whose streaming support is a separate question this milestone has not verified.

---

### 102.4: Tests

**Files modified:**

- `tests/unit/test_realtime_auto_select.py` *(new)*

**Deliverables:**

- A non-native model whose engine has an RT worker **is** selected (the wrapper case)
- A larger model whose engine has **no** RT worker is **not** selected — the exact production failure
- Native beats non-native when both are servable, regardless of size
- Among equals, larger wins (preserves the existing accuracy heuristic)
- No servable model → `ValueError` naming the cause, not a silent bad pick
- Language filtering narrows but never widens beyond servable

---

## Non-Goals

- **Latency-aware ranking.** `max(..., size_bytes)` is an accuracy heuristic borrowed from batch, and it is questionable for realtime — a wrapper-backed 18.7 GB model means seconds per utterance. But once the hard filter is correct, candidates are already limited to engines the operator chose to run for realtime, so the risk is bounded. Revisit if `rtf_gpu` (present in model YAML, absent from the DB schema) is ever surfaced.
- **Deriving `native_streaming` from engine capabilities** rather than hand-maintained YAML. The engine already knows via `supports_native_streaming_decode()`, and the two have visibly drifted — but wiring that through model ingestion is its own change.
- **ONNX engine streaming support** — unverified, left alone.
- **Changing explicit model selection.** That path works and is untouched.

---

## Verification

```bash
# With a NeMo RT worker live and no vllm-asr worker, auto-select must
# choose the NeMo model rather than the largest overall.
docker exec -i dalston-gateway-1 python3 -c "
import asyncio
from dalston.gateway.api.v1._realtime_common import resolve_rt_routing
async def m():
    rt = await resolve_rt_routing(requested_model=None, language='en')
    print('routing_model =', rt.routing_model)
    print('engine_id     =', rt.model_engine_id)
asyncio.run(m())"
# want: nvidia/parakeet-tdt-0.6b-v3 / nemo
```

Console: select **Any available** on `/console/realtime/live` and start a session — it must stream rather than report "No realtime workers available".

---

## Checkpoint

- [ ] Auto-select filters on `live_rt_engine_ids`, not `native_streaming`
- [ ] `native_streaming` ranks candidates instead of gating them
- [ ] Language filtering never widens past servable models
- [ ] No servable engine raises a `ValueError` naming the cause
- [ ] NeMo TDT model YAMLs declare `native_streaming: true`
- [ ] "Any available" starts a session against a live NeMo worker
