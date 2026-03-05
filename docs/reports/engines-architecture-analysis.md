# Engines Architecture Analysis: Registry, Runtimes, and Batch/Realtime Unification

**Date**: 2026-03-03
**Scope**: Batch engine registry + runtime model management (M30/M31/M36/M41) and its impact on real-time engines

---

## Executive Summary

The batch engine architecture has evolved significantly through milestones M30-M41, introducing a **registry-based discovery system**, a **static model catalog**, **capability-driven routing**, and **runtime model management** with 5 transcription runtimes. These changes are well-designed for batch processing but have **not been extended to real-time engines**, creating a growing divergence between the two subsystems. The real-time engines still work correctly but are now architecturally behind: they lack dynamic model management, capability declarations, and catalog integration.

This report analyzes: (1) whether changes have broken real-time engines, (2) whether the same approach applies to real-time, and (3) opportunities for unification.

---

## 1. Current Architecture: The Two Worlds

### Batch Engine Architecture (Post-M36)

```
                      ┌──────────────────────────────┐
                      │  Generated Catalog (JSON)     │
                      │  models/*.yaml → catalog.json │
                      │  Engine→Runtime→Model mapping  │
                      └──────────┬───────────────────┘
                                 │ resolves model → runtime + runtime_model_id
                                 ▼
┌──────────┐    ┌──────────────────────┐    ┌──────────────────────┐
│ API      │───▶│ Orchestrator         │───▶│ Redis Streams        │
│ Request  │    │  engine_selector.py  │    │ dalston:stream:{id}  │
│ model=X  │    │  dag.py              │    └──────────┬───────────┘
└──────────┘    │  scheduler.py        │               │
                └──────────────────────┘               ▼
                         ▲                  ┌──────────────────────┐
                         │ heartbeat        │ Engine (SDK runner)  │
                         │ capabilities     │  ModelManager        │
                ┌────────┴──────────┐       │  acquire(model_id)   │
                │ Batch Registry    │       │  S3ModelStorage      │
                │ dalston:batch:*   │◀──────│  engine.yaml caps    │
                │ EngineCapabilities│       └──────────────────────┘
                └───────────────────┘
```

**Key components:**

| Component | Purpose | Location |
|-----------|---------|----------|
| `engine.yaml` | Declares runtime, capabilities, hardware requirements | `engines/stt-*/*/engine.yaml` |
| `EngineCapabilities` | Pydantic schema for what an engine can do | `engine_sdk/types.py` |
| `BatchEngineRegistry` | Heartbeat-based registration in Redis | `engine_sdk/registry.py` |
| `EngineCatalog` | Static model→runtime mapping from `generated_catalog.json` | `orchestrator/catalog.py` |
| `ModelManager` | TTL-based model lifecycle with ref counting + LRU eviction | `engine_sdk/model_manager.py` |
| `S3ModelStorage` | S3-backed model cache with atomic downloads | `engine_sdk/model_storage.py` |
| `FasterWhisperModelManager` | Concrete manager for CTranslate2 Whisper models | `engine_sdk/managers/faster_whisper.py` |
| `HFTransformersModelManager` | Concrete manager for HF pipeline models | `engine_sdk/managers/hf_transformers.py` |
| `engine_selector.py` | Capability-driven engine ranking and selection | `orchestrator/engine_selector.py` |

### Real-Time Engine Architecture (Unchanged)

```
┌──────────┐    ┌──────────────────────┐    ┌──────────────────────┐
│ WS       │───▶│ Gateway              │───▶│ Session Router       │
│ Connect  │    │  realtime.py         │    │  allocator.py        │
│ model=Y  │    │  acquire_worker()    │    │  least-loaded pick   │
└──────────┘    └──────────────────────┘    └──────────┬───────────┘
                                                       │ direct WS proxy
                                                       ▼
                ┌───────────────────┐       ┌──────────────────────┐
                │ RT Worker Registry│       │ RealtimeEngine       │
                │ dalston:realtime:*│◀──────│  load_models()       │
                │ models, languages │       │  transcribe(audio)   │
                │ (flat strings)    │       │  HARDCODED models    │
                └───────────────────┘       └──────────────────────┘
```

**What real-time engines DON'T have:**

| Batch has | Real-time equivalent | Gap |
|-----------|---------------------|-----|
| `engine.yaml` with `runtime:` field | No engine.yaml at all | No declarative capabilities |
| `EngineCapabilities` schema | Flat strings (`models_loaded`, `languages_supported`) | No structured validation |
| `EngineCatalog` integration | None — models hardcoded in Python | No dynamic model resolution |
| `ModelManager` (TTL + LRU) | Models loaded once at startup, never swapped | No runtime model flexibility |
| `S3ModelStorage` | None — models downloaded at Docker build time | No runtime model provisioning |
| `runtime_model_id` in task config | Fixed `model_variant` from query params | No orchestrator-driven model selection |
| Capability-driven stage skipping | Not applicable (single-stage) | N/A |

---

## 2. Question 1: Have Changes Inadvertently Broken Real-Time Engines?

### Answer: No, but they have been left behind

The batch and real-time subsystems are **cleanly separated** at every level:

**Redis namespace isolation:**

- Batch: `dalston:batch:engines`, `dalston:batch:engine:{id}`, `dalston:stream:{id}`
- Real-time: `dalston:realtime:workers`, `dalston:realtime:worker:{id}`, `dalston:realtime:session:{id}`

**SDK isolation:**

- Batch: `dalston/engine_sdk/` — `Engine` base class, `EngineRunner`, sync Redis
- Real-time: `dalston/realtime_sdk/` — `RealtimeEngine` base class, WebSocket server, async Redis

**No shared mutable state:** The two registries, the two SDKs, and the two sets of engine implementations share no Redis keys, no base classes, and no runner logic. Changes to `engine_sdk` cannot break `realtime_sdk`.

**However, the divergence creates operational risks:**

1. **No capability validation for RT:** The Gateway's `realtime.py` passes `model` as a raw query parameter. If the requested model isn't loaded on any worker, the Session Router silently assigns any available worker. There's no equivalent of the batch `engine_selector.py` that validates capabilities first.

2. **No model catalog for RT:** A user requesting `model=parakeet-tdt-1.1b` in a batch job gets catalog resolution to the correct runtime. The same model name in a real-time request is treated as an opaque string matched against `models_loaded` — a list of arbitrary names each RT engine publishes.

3. **No standardized model names:** Batch engines use catalog-defined names (`Systran/faster-whisper-large-v3-turbo`). RT engines use ad-hoc names (`faster-whisper-large-v3`, `faster-whisper-distil-large-v3`). The same underlying model has different identifiers depending on whether it's accessed via batch or real-time.

4. **No runtime model swapping for RT:** Batch engines can load any model variant at runtime via `ModelManager.acquire()`. RT engines load a fixed set at startup. To change models, you must redeploy the container.

**Verdict:** Nothing is broken today. But the two subsystems are **drifting apart** in capabilities, and users will encounter inconsistencies when using both batch and real-time for the same audio (e.g., real-time for live transcription followed by batch enhancement).

---

## 3. Question 2: Is the Same Approach Applicable to Real-Time?

### Answer: Largely yes, with adaptations for the streaming lifecycle

The core batch innovations can be mapped to real-time:

### 3.1 What transfers directly

| Batch concept | RT adaptation | Complexity |
|--------------|---------------|------------|
| `engine.yaml` with structured capabilities | Add `engine.yaml` to `engines/stt-rt/*/` | Low — just YAML files |
| `EngineCapabilities` schema | Reuse same schema (already has `max_concurrency`, `supports_streaming`) | Low — schema already supports it |
| `EngineCatalog` model resolution | Session Router uses catalog to resolve `model=X` → worker with correct runtime | Medium — needs `session_router/` changes |
| `S3ModelStorage` for model provisioning | RT engines download models from S3 at startup or on-demand | Low — library is runtime-agnostic |
| Standardized model naming | Both batch and RT use catalog model IDs | Low — naming convention |

### 3.2 What needs adaptation

**`ModelManager` for real-time engines:**

The batch `ModelManager` already has reference counting — the exact primitive needed for RT. The adaptation:

```
Batch:                              Real-time:
  acquire(model_id)                   acquire(model_id)      ← same
  process(task)                       session runs (minutes)  ← long-lived ref
  release(model_id)                   release(model_id)      ← on session end
  model evicted after TTL             model evicted after TTL ← same
```

The key difference: **batch refs are seconds, RT refs are minutes to hours**. This makes TTL eviction less aggressive for RT (models stay loaded as long as sessions use them), which is actually the desired behavior.

**Challenge: Model swap latency during active sessions.**
If a worker is serving 3 sessions on `model-A` and a new session requests `model-B`, the worker must either:

- (a) Load `model-B` alongside `model-A` (requires VRAM headroom) — `max_loaded=2` handles this
- (b) Reject the session and let the allocator find another worker — current behavior, fine
- (c) Wait for `model-A` sessions to drain — too slow for real-time

Option (a) is already supported by `ModelManager` with `max_loaded`. Option (b) works today. No new mechanisms needed.

**Heartbeat enrichment:**

Batch heartbeats include `loaded_model` and `local_cache`. RT heartbeats would add the same fields, plus the existing `active_sessions` and `capacity`. This gives the Session Router the information to route model-specific requests to workers that already have the model hot.

### 3.3 What doesn't apply

| Batch concept | Why it doesn't apply to RT |
|--------------|---------------------------|
| Redis Streams task queuing | RT uses direct WebSocket proxying — no queue |
| Task DAG / multi-stage pipeline | RT is single-stage (transcribe only) |
| Capability-driven stage skipping (M31) | No stages to skip |
| `runtime_model_id` in task config | RT model selected at connection time, not per-task |

### Pros and cons of applying the batch approach to RT

**Pros:**

- Unified model naming: users use the same model IDs for batch and RT
- Dynamic model loading: deploy a generic RT container, load models on-demand from S3
- Capability validation: reject unsupported model/language combos at the Gateway, not silently at the worker
- Operational simplicity: one model catalog, one set of `engine.yaml` files, one capability schema
- Cost reduction: fewer specialized Docker images, share GPU across models via TTL eviction

**Cons:**

- First-session latency: if the model isn't preloaded, the first session waits for model download + load (seconds to minutes)
- VRAM pressure: multiple models loaded on one GPU increases OOM risk for RT (where latency matters more)
- Complexity: RT engines are currently simple (`load_models()` + `transcribe()`); adding ModelManager, S3 storage, and capabilities adds moving parts
- Diminishing returns: RT deployments typically run 1-2 model variants, not the 10+ that batch handles

---

## 4. Question 3: Opportunities to Unify Batch and Real-Time

### 4.1 Concrete unification opportunities

#### A. Shared model catalog and naming (Low effort, high impact)

**Current state:** Batch models use catalog IDs (`Systran/faster-whisper-large-v3-turbo`), RT models use ad-hoc names (`faster-whisper-large-v3`).

**Proposal:** RT engines publish catalog model IDs in their `models_loaded` heartbeat. The Session Router resolves incoming `model=X` through the catalog, same as the orchestrator.

**Impact:** Users use one model name everywhere. The same `model=parakeet-tdt-1.1b` works in both `POST /v1/audio/transcriptions` and `WS /v1/audio/transcriptions/stream`.

**Pros:**

- No SDK changes needed — just naming convention
- Eliminates user confusion
- Enables hybrid mode (RT → batch enhancement) with consistent model references

**Cons:**

- Requires updating RT engine code to use catalog names
- Minor: RT workers would need catalog awareness to validate model names

#### B. Shared `engine.yaml` + `EngineCapabilities` (Low effort, medium impact)

**Current state:** Batch engines have `engine.yaml` with structured capabilities. RT engines have none.

**Proposal:** Add `engine.yaml` files to `engines/stt-rt/*/`. RT engines parse them in `RealtimeEngine.get_capabilities()` (mirror `Engine.get_capabilities()`). RT worker registration includes capabilities.

**Impact:** Session Router gains structured capability data for smarter allocation. Gateway can validate language/model support before proxying.

**Pros:**

- Reuses existing `EngineCapabilities` schema (already has `supports_streaming`, `max_concurrency`)
- Enables capability validation at the Gateway (reject bad requests early)
- Operational visibility: same capability schema in monitoring for both batch and RT

**Cons:**

- `engine.yaml` for RT engines is partially redundant (RT capabilities are simpler)
- Session Router needs to parse capabilities (currently uses flat strings)

#### C. Shared `ModelManager` for RT engines (Medium effort, high impact)

**Current state:** RT engines hardcode models in `load_models()`:

```python
# engines/stt-rt/faster-whisper/engine.py
MODELS = {
    "faster-whisper-distil-large-v3": "Systran/faster-distil-whisper-large-v3",
    "faster-whisper-large-v3": "Systran/faster-whisper-large-v3",
}
def load_models(self):
    for name, hf_id in self.MODELS.items():
        self._models[name] = WhisperModel(hf_id, ...)
```

**Proposal:** RT engines use `FasterWhisperModelManager` (or equivalent) to load models on-demand:

```python
def load_models(self):
    self._manager = FasterWhisperModelManager.from_env()
    # Preload default model
    if preload := os.environ.get("DALSTON_MODEL_PRELOAD"):
        self._manager.acquire(preload)
        self._manager.release(preload)

def transcribe(self, audio, language, model_variant, vocabulary=None):
    model = self._manager.acquire(model_variant)
    try:
        segments, info = model.transcribe(audio, ...)
        return TranscribeResult(...)
    finally:
        self._manager.release(model_variant)
```

**Impact:** Same model management for batch and RT. Models loaded/evicted dynamically. One container image serves any model variant.

**Pros:**

- Eliminates per-model RT Docker images (currently: `stt-rt-transcribe-parakeet-rnnt-0.6b`, `stt-rt-transcribe-parakeet-rnnt-1.1b`, etc.)
- Reference counting already handles concurrent sessions perfectly
- TTL eviction frees VRAM when a model is no longer requested
- Enables warm model routing: Session Router checks `loaded_model` in heartbeat

**Cons:**

- First-request latency for cold models (mitigated by `DALSTON_MODEL_PRELOAD`)
- Batch `ModelManager` is sync (threading), RT is async (asyncio) — need async wrapper or `asyncio.to_thread()`
- Multiple models on one GPU increases VRAM pressure (manageable via `max_loaded`)

#### D. Unified Engine base class (High effort, debatable value)

**Current state:** Two completely separate base classes with different signatures:

| | Batch `Engine` | Real-time `RealtimeEngine` |
|---|---|---|
| Core method | `process(TaskInput) → TaskOutput` | `transcribe(np.ndarray, ...) → TranscribeResult` |
| I/O model | File-based (S3 paths) | Streaming (WebSocket frames) |
| Concurrency | One task at a time | Multiple concurrent sessions |
| Lifecycle | Short-lived per task | Long-lived server |
| Runner | `EngineRunner` (sync, stream polling) | WebSocket server (async) |

**Proposal:** Create a shared `TranscriptionModel` protocol that both batch and RT engines wrap:

```python
class TranscriptionModel(Protocol):
    def transcribe(self, audio: np.ndarray | Path, **kwargs) -> TranscribeResult: ...
    def get_capabilities(self) -> EngineCapabilities: ...
```

Then:

- Batch `Engine.process()` calls `model.transcribe(audio_path, ...)`
- RT `RealtimeEngine.transcribe()` calls `model.transcribe(audio_array, ...)`

**Pros:**

- One model implementation works for both batch and RT
- Ensures identical transcription results regardless of access mode
- Reduces code duplication in engine implementations

**Cons:**

- **High coupling between subsystems** — changes to the shared interface affect both
- Batch and RT have legitimately different input types (`Path` vs `np.ndarray`)
- Batch engines do more than transcribe (alignment, diarization, PII) — the shared interface only covers transcription
- The "glue code" (runner/session handler) is where the real complexity lives, not in the model call
- Risk of premature abstraction — the two codepaths may diverge further as features evolve

**Verdict: Not recommended as a near-term priority.** The concrete gains from A, B, and C provide 80% of the value. A shared model protocol is a nice-to-have for the transcribe stage but doesn't generalize to other pipeline stages.

### 4.2 Recommended approach

**Phase 1 (Quick wins — minimal code changes):**

1. Add `engine.yaml` to all RT engines in `engines/stt-rt/*/`
2. Standardize model names to use catalog IDs
3. RT heartbeats include `EngineCapabilities` JSON (same schema as batch)

**Phase 2 (Model management — moderate refactor):**
4. Integrate `ModelManager` into `RealtimeEngine` base class
5. Integrate `S3ModelStorage` for RT model provisioning
6. Session Router uses `loaded_model` from heartbeat for warm-model routing
7. Consolidate per-model RT Docker images into per-runtime images

**Phase 3 (Deep integration — if warranted by usage patterns):**
8. Session Router uses `EngineCatalog` for model resolution
9. Gateway validates RT model/language requests against capabilities
10. Shared `TranscriptionModel` protocol for faster-whisper (shared between `engines/stt-transcribe/faster-whisper/engine.py` and `engines/stt-rt/faster-whisper/engine.py`)

---

## 5. The Five Transcription Runtimes

For reference, here are the current runtimes and their batch/RT coverage:

| Runtime | Batch engine | RT engine | Key trait |
|---------|-------------|-----------|-----------|
| `faster-whisper` | `engines/stt-transcribe/faster-whisper/` | `engines/stt-rt/faster-whisper/` | 99 languages, CTranslate2, RTF 0.03 GPU |
| `nemo` | `engines/stt-transcribe/parakeet/` | `engines/stt-rt/parakeet/` | English, native word timestamps, RTF 0.0006 GPU |
| `nemo-onnx` | `engines/stt-transcribe/parakeet-onnx/` | `engines/stt-rt/parakeet-onnx/` | 12x smaller image, ONNX Runtime, RTF 0.0003 GPU |
| `hf-asr` | `engines/stt-transcribe/hf-asr/` | None | 10k+ HF models, generic Transformers pipeline |
| `vllm-asr` | `engines/stt-transcribe/vllm-asr/` | `engines/stt-rt/voxtral/` | Audio LLMs (Voxtral, Qwen2-Audio), GPU required |

**Observations:**

- `faster-whisper`, `nemo`, and `nemo-onnx` have both batch and RT implementations — these are the prime candidates for unification
- `hf-asr` is batch-only — adding RT support would be straightforward with the unified model manager
- `vllm-asr` has limited RT applicability due to high latency (5s warm start) and no streaming support

---

## 6. Summary: Pros and Cons by Approach

### Approach A: Keep separate (status quo)

| Pros | Cons |
|------|------|
| No refactoring needed | Growing divergence in capabilities |
| Each subsystem optimized for its use case | Inconsistent model naming confuses users |
| Simple RT engines stay simple | No dynamic model management for RT |
| Independent evolution | Operational overhead: separate Docker images per RT model |

### Approach B: Partial unification (Phases 1-2)

| Pros | Cons |
|------|------|
| Consistent model naming and capabilities | Some refactoring of RT engines |
| Dynamic model loading for RT | First-request latency for cold models |
| Fewer Docker images | `ModelManager` sync/async adaptation needed |
| Warm-model routing reduces latency | Additional complexity in RT base class |
| 80% of unification value for 20% of effort | |

### Approach C: Full unification (Phase 3)

| Pros | Cons |
|------|------|
| One model implementation for batch+RT | High coupling between subsystems |
| Guaranteed identical transcription results | Premature abstraction risk |
| Maximum code reuse | Only benefits transcription stage |
| Unified operational model | Significant refactoring of both SDKs |

**Recommendation:** Pursue **Approach B** (Phases 1-2). It delivers the most value — unified naming, dynamic model management, fewer images — with manageable effort and no architectural risk. Phase 3 can be evaluated later once Phase 2 is in production and usage patterns are clearer.
