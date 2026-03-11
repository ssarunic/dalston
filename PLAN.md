# Plan: Unified TranscriberParams + Engine Naming Consistency

## Guiding Principle

**Runtimes are infrastructure, models are configuration.** Adding a new model to an existing runtime should be a config change + adapter, not a new engine.

The system currently names engines after models (Parakeet, Voxtral) when they should be named after runtimes (NeMo, vLLM). This conflates what runs (model) with what runs it (runtime).

## Overview

Two steps, each independently shippable:

1. **Step 1: TranscriberParams + class renames** — add unified input type, rename all engine classes to runtime-based naming
2. **Step 2: Voxtral absorption + RivaCore extraction** — eliminate model-specific engines that duplicate runtime-generic ones

---

## Step 1: TranscriberParams + Class Renames

### Phase 1a: Add `TranscriberParams`

**Files changed:**
- `dalston/common/pipeline_types.py` — add `TranscriberParams` class

```python
class TranscriberParams(BaseModel):
    """Runtime-neutral transcription parameters.

    Constructed by the gateway (batch) or session layer (RT).
    Engines take what they need and ignore the rest.
    """
    language: str | None = None
    task: str = "transcribe"
    model_id: str | None = None
    temperature: float | list[float] = 0.0
    beam_size: int | None = None
    best_of: int | None = None
    vocabulary: list[str] | None = None
    prompt: str | None = None
    word_timestamps: bool = True
    suppress_blank: bool = True
    vad_filter: bool = True
```

No consumers yet — just the type definition and tests.

### Phase 1b: Wire into Batch SDK

**Files changed:**
- `dalston/engine_sdk/types.py` — add `transcriber_params: TranscriberParams` field to `EngineInput`
- `dalston/engine_sdk/runner.py` — construct `TranscriberParams` from the task config dict when building `EngineInput` (keep `config` dict for backward compat during migration)

**Engine migrations (each engine switches from `config.get(...)` to `transcriber_params.*`):**
- `engines/stt-transcribe/faster-whisper/engine.py`
- `engines/stt-transcribe/parakeet/engine.py`
- `engines/stt-transcribe/parakeet-onnx/engine.py`
- `engines/stt-transcribe/voxtral/engine.py`
- `engines/stt-transcribe/vllm-asr/engine.py`
- `engines/stt-transcribe/hf-asr/engine.py`
- `engines/stt-transcribe/riva/engine.py`

### Phase 1c: Wire into Realtime SDK

**Files changed:**
- `dalston/realtime_sdk/session.py` — change `TranscribeCallback` from `(audio, language, model, vocabulary)` to `(audio, params: TranscriberParams)`
- `dalston/realtime_sdk/base_transcribe.py` — update `transcribe_v1()` signature to `(audio: np.ndarray, params: TranscriberParams)`
- `dalston/realtime_sdk/base.py` — construct `TranscriberParams` from `SessionConfig` in `_handle_connection()`

**Engine migrations:**
- `engines/stt-rt/faster-whisper/engine.py`
- `engines/stt-rt/parakeet/engine.py`
- `engines/stt-rt/parakeet-onnx/engine.py`
- `engines/stt-rt/voxtral/engine.py`
- `engines/stt-rt/riva/engine.py`

### Phase 1d: Rename classes to runtime-based naming

All renames are mechanical find-and-replace within each engine + its tests + unified runner imports.

**Runtime strings (no changes — already correct in engine.yaml):**
- `faster-whisper`, `nemo`, `nemo-onnx`, `vllm-asr`, `hf-asr`, `riva`

**Core renames:**

| Before | After | File |
|---|---|---|
| `TranscribeCore` | `FasterWhisperCore` | `dalston/engine_sdk/cores/faster_whisper_core.py` |
| `TranscribeConfig` | `FasterWhisperConfig` | same file |
| `ParakeetCore` | `NemoCore` | rename file to `nemo_core.py` |
| `ParakeetOnnxCore` | `NemoOnnxCore` | rename file to `nemo_onnx_core.py` |

**Batch engine renames:**

| Before | After |
|---|---|
| `WhisperEngine` | `FasterWhisperBatchEngine` |
| `ParakeetEngine` | `NemoBatchEngine` |
| `ParakeetOnnxEngine` | `NemoOnnxBatchEngine` |
| `HFASREngine` | `HfAsrBatchEngine` |
| `VLLMASREngine` | `VllmBatchEngine` |
| `RivaBatchEngine` | no change |

**Realtime engine renames:**

| Before | After |
|---|---|
| `WhisperStreamingEngine` | `FasterWhisperRealtimeEngine` |
| `ParakeetStreamingEngine` | `NemoRealtimeEngine` |
| `ParakeetOnnxStreamingEngine` | `NemoOnnxRealtimeEngine` |
| `RivaRealtimeEngine` | no change |

Voxtral engines are NOT renamed — they're absorbed in Step 2.

**Managers** — no changes needed (`FasterWhisperModelManager`, `NeMoModelManager`, `NeMoOnnxModelManager`, `HFTransformersModelManager` are already correct).

**Unified runners** — import updates only in `engines/stt-unified/{faster-whisper,parakeet,parakeet-onnx}/runner.py`.

**Gateway/Orchestrator** — no changes. These use runtime strings, not class names.

---

## Step 2: Voxtral Absorption + RivaCore Extraction

### Phase 2a: Absorb Voxtral batch into VllmBatchEngine

The standalone `engines/stt-transcribe/voxtral/` uses Transformers directly but reports `runtime: vllm-asr`. It duplicates functionality that `VllmBatchEngine` already provides via its `VoxtralAdapter`. This is a model pretending to be a runtime.

**Action:**
- Delete `engines/stt-transcribe/voxtral/` entirely
- Voxtral batch transcription is already handled by `VllmBatchEngine` + `VoxtralAdapter` in `engines/stt-transcribe/vllm-asr/adapters/`
- Update docker-compose: remove `stt-batch-transcribe-voxtral-mini-3b` service, point voxtral batch workloads to `stt-batch-transcribe-vllm-asr` with `DALSTON_DEFAULT_MODEL_ID=mistralai/Voxtral-Mini-3B-2507`
- Delete `tests/unit/test_voxtral_engine.py` (batch)
- Update `dalston/gateway/services/hf_resolver.py` if it references voxtral directly

### Phase 2b: Absorb Voxtral RT into HF-ASR / Transformers RT engine

The Voxtral RT engine (`engines/stt-rt/voxtral/`) uses `transformers.VoxtralRealtimeForConditionalGeneration`. It's the same runtime as hf-asr (Transformers) but with a model-specific wrapper.

**Action:**
- Create `HfAsrRealtimeEngine` (new RT engine for the `hf-asr` runtime) in `engines/stt-rt/hf-asr/`
- Move Voxtral RT logic into the new engine as model-specific handling (the Voxtral realtime model needs special prompt construction and output parsing, similar to how vllm-asr has adapters)
- Delete `engines/stt-rt/voxtral/`
- Update docker-compose: rename `stt-rt-voxtral` service to `stt-rt-hf-asr`, configure with `DALSTON_DEFAULT_MODEL_ID=mistralai/Voxtral-Mini-4B-Realtime-2602`

This means "voxtral" no longer appears as an engine anywhere — it's just a model that runs on the `hf-asr` (Transformers) runtime.

### Phase 2c: Extract `RivaCore`

**New file:** `dalston/engine_sdk/cores/riva_core.py`

Extract shared logic from both Riva engines:
- gRPC channel management (connect, health check, shutdown)
- Audio format conversion (float32 → int16 bytes)
- `RecognitionConfig` construction from `TranscriberParams`
- Response parsing → `Transcript`

```python
class RivaCore:
    """Shared Riva NIM gRPC client for batch and realtime engines."""

    def __init__(self, uri: str = "localhost:50051", chunk_ms: int = 100): ...
    def transcribe(self, audio: np.ndarray | bytes, params: TranscriberParams) -> Transcript: ...
    def health_check(self) -> dict[str, Any]: ...
    def shutdown(self) -> None: ...
```

**Files changed:**
- `engines/stt-transcribe/riva/engine.py` — simplify to thin adapter delegating to `RivaCore`
- `engines/stt-rt/riva/engine.py` — same
- New tests for `RivaCore` in isolation

After this, Riva is structurally identical to faster-whisper and nemo (Core + Batch adapter + RT adapter).

---

## End State

After both steps, the engine taxonomy is clean:

| Runtime | Core | Batch Engine | RT Engine | Models |
|---|---|---|---|---|
| faster-whisper | `FasterWhisperCore` | `FasterWhisperBatchEngine` | `FasterWhisperRealtimeEngine` | Whisper variants (large-v3, turbo, etc.) |
| nemo | `NemoCore` | `NemoBatchEngine` | `NemoRealtimeEngine` | Parakeet, Canary (future) |
| nemo-onnx | `NemoOnnxCore` | `NemoOnnxBatchEngine` | `NemoOnnxRealtimeEngine` | ONNX-exported NeMo models |
| vllm-asr | — | `VllmBatchEngine` | — | Voxtral, Qwen2-Audio, extensible via adapters |
| hf-asr | — | `HfAsrBatchEngine` | `HfAsrRealtimeEngine` | Any HF ASR model, Voxtral-Realtime |
| riva | `RivaCore` | `RivaBatchEngine` | `RivaRealtimeEngine` | Whatever NIM serves |

**Model selection is config, not code:**
- `NemoCore` loads Parakeet OR Canary based on `model_id` in `TranscriberParams`
- `VllmBatchEngine` loads Voxtral OR Qwen2-Audio based on adapter selection
- `FasterWhisperCore` loads large-v3 OR base OR turbo based on `model_id`

Adding a new model to an existing runtime = config change + adapter (if needed). No new engine required.

## What is NOT in scope

- **Directory renames** (e.g., `engines/stt-rt/parakeet/` → `engines/stt-rt/nemo/`). These would break Dockerfiles, compose service names, CI, and image tags. The directory name can represent the default model family.
- **Runtime string changes**. Current strings are stored in Redis, used in routing, referenced in configs. They're already reasonable.

## Commit strategy

One commit per phase. Each is independently testable (`make test` passes after each).
