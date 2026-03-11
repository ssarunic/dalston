# Plan: Unified TranscriberParams + Engine Naming Consistency

## Overview

Two related changes:
1. **TranscriberParams** — a canonical input type for all transcription engines (the input analog to `Transcript` output)
2. **Engine naming** — rename classes from model-based names to runtime-based names, make Batch/Realtime suffix consistent

These are split into 4 phases to keep each commit reviewable and testable independently.

---

## Phase 1: Add `TranscriberParams` to `dalston/common/pipeline_types.py`

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

**Tests:** Unit tests for TranscriberParams construction, serialization, defaults.

---

## Phase 2: Wire `TranscriberParams` into SDK base classes

### 2a: Batch SDK — replace `config: dict` with `TranscriberParams`

**Files changed:**
- `dalston/engine_sdk/types.py` — add `transcriber_params: TranscriberParams` field to `EngineInput`
- `dalston/engine_sdk/base_transcribe.py` — update `transcribe_audio()` signature docs
- `dalston/engine_sdk/runner.py` — construct `TranscriberParams` from the task config dict when building `EngineInput` (backward compat: keep `config` dict populated too for now)

Each batch engine currently does `config.get("language")`, `config.get("beam_size", 5)`, etc. from a raw dict. After this change, engines can use `engine_input.transcriber_params.language` instead. The raw `config` dict stays for backward compat during migration.

**Engine migrations (one per engine, can be separate commits):**
- `engines/stt-transcribe/faster-whisper/engine.py` — use `transcriber_params` instead of `config` dict
- `engines/stt-transcribe/parakeet/engine.py` — same
- `engines/stt-transcribe/parakeet-onnx/engine.py` — same
- `engines/stt-transcribe/voxtral/engine.py` — same
- `engines/stt-transcribe/vllm-asr/engine.py` — same
- `engines/stt-transcribe/hf-asr/engine.py` — same
- `engines/stt-transcribe/riva/engine.py` — same

### 2b: Realtime SDK — replace positional args with `TranscriberParams`

**Files changed:**
- `dalston/realtime_sdk/session.py` — change `TranscribeCallback` signature from `(audio, language, model, vocabulary)` to `(audio, params: TranscriberParams)`. Update the two call sites in `_transcribe_and_send` and the streaming partial path.
- `dalston/realtime_sdk/base_transcribe.py` — update `transcribe()` and `transcribe_v1()` signatures:
  ```python
  # Before:
  def transcribe_v1(self, audio, language, model_variant, vocabulary) -> Transcript
  # After:
  def transcribe_v1(self, audio: np.ndarray, params: TranscriberParams) -> Transcript
  ```
- `dalston/realtime_sdk/base.py` — update `_handle_connection()` to construct `TranscriberParams` from `SessionConfig`

**Engine migrations (one per engine):**
- `engines/stt-rt/faster-whisper/engine.py` — update `transcribe_v1()` to accept `TranscriberParams`
- `engines/stt-rt/parakeet/engine.py` — same
- `engines/stt-rt/parakeet-onnx/engine.py` — same
- `engines/stt-rt/voxtral/engine.py` — same
- `engines/stt-rt/riva/engine.py` — same

**Tests:** Update all test files that mock/call transcribe_v1 or TranscribeCallback.

---

## Phase 3: Rename classes to runtime-based naming

All renames are mechanical find-and-replace within each engine + its tests + the unified runner that imports it.

### Runtime string decisions (no changes needed — already correct):
- `faster-whisper` — keep (it IS the library name)
- `nemo` — keep (already in engine.yaml)
- `nemo-onnx` — keep (already in engine.yaml)
- `vllm-asr` — keep (distinguishes from generic vllm)
- `hf-asr` — keep (distinguishes from generic transformers/hf)
- `riva` — keep

### Class renames:

**Cores:**
| Before | After | File |
|---|---|---|
| `TranscribeCore` | `FasterWhisperCore` | `dalston/engine_sdk/cores/faster_whisper_core.py` |
| `TranscribeConfig` | `FasterWhisperConfig` | same file |
| `ParakeetCore` | `NemoCore` | `dalston/engine_sdk/cores/parakeet_core.py` → rename file to `nemo_core.py` |
| `ParakeetOnnxCore` | `NemoOnnxCore` | `dalston/engine_sdk/cores/parakeet_onnx_core.py` → rename file to `nemo_onnx_core.py` |

**Managers (no changes needed — already correct):**
| Class | Status |
|---|---|
| `FasterWhisperModelManager` | already correct |
| `NeMoModelManager` | already correct |
| `NeMoOnnxModelManager` | already correct |
| `HFTransformersModelManager` | already correct |

**Batch engines:**
| Before | After | File |
|---|---|---|
| `WhisperEngine` | `FasterWhisperBatchEngine` | `engines/stt-transcribe/faster-whisper/engine.py` |
| `ParakeetEngine` | `NemoBatchEngine` | `engines/stt-transcribe/parakeet/engine.py` |
| `ParakeetOnnxEngine` | `NemoOnnxBatchEngine` | `engines/stt-transcribe/parakeet-onnx/engine.py` |
| `VoxtralEngine` | `VoxtralBatchEngine` | `engines/stt-transcribe/voxtral/engine.py` |
| `HFASREngine` | `HfAsrBatchEngine` | `engines/stt-transcribe/hf-asr/engine.py` |
| `VLLMASREngine` | `VllmAsrBatchEngine` | `engines/stt-transcribe/vllm-asr/engine.py` |
| `RivaBatchEngine` | no change | already correct |

**Realtime engines:**
| Before | After | File |
|---|---|---|
| `WhisperStreamingEngine` | `FasterWhisperRealtimeEngine` | `engines/stt-rt/faster-whisper/engine.py` |
| `ParakeetStreamingEngine` | `NemoRealtimeEngine` | `engines/stt-rt/parakeet/engine.py` |
| `ParakeetOnnxStreamingEngine` | `NemoOnnxRealtimeEngine` | `engines/stt-rt/parakeet-onnx/engine.py` |
| `VoxtralStreamingEngine` | `VoxtralRealtimeEngine` | `engines/stt-rt/voxtral/engine.py` |
| `RivaRealtimeEngine` | no change | already correct |

**Unified runners** (import updates only):
- `engines/stt-unified/faster-whisper/runner.py` — update class imports
- `engines/stt-unified/parakeet/runner.py` — update class imports
- `engines/stt-unified/parakeet-onnx/runner.py` — update class imports

**Test files** — update class imports and instantiation in all affected test files.

**Gateway/Orchestrator** — no changes needed. These reference runtime strings (`"faster-whisper"`, `"nemo"`, etc.), not class names. Runtime strings stay the same.

---

## Phase 4: Extract `RivaCore` (shared inference logic)

**New file:** `dalston/engine_sdk/cores/riva_core.py`

Extract from both Riva engines:
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
- `engines/stt-transcribe/riva/engine.py` — simplify to delegate to `RivaCore`
- `engines/stt-rt/riva/engine.py` — simplify to delegate to `RivaCore`
- New tests for `RivaCore` in isolation

This makes Riva structurally identical to faster-whisper and nemo (both already have cores).

---

## What is NOT in scope

- **Directory renames** (e.g., `engines/stt-rt/parakeet/` → `engines/stt-rt/nemo/`). These would break Dockerfiles, compose service names, CI, and image tags. Not worth the churn — the directory name represents the default model, which is fine.
- **Voxtral absorption into vllm-asr**. The Voxtral engines use Transformers, not vLLM. They're model-specific implementations that happen to share a runtime string. Absorbing them requires either making them actually use vLLM (different inference path) or creating a Transformers core. Separate milestone.
- **Runtime string changes**. The current strings (`faster-whisper`, `nemo`, `nemo-onnx`, `vllm-asr`, `hf-asr`, `riva`) are already reasonable and are stored in Redis, used in routing, referenced in configs. Changing them has high blast radius for low value.

## Commit strategy

One commit per phase. Each phase is independently testable:
- Phase 1: `make test` passes (new type, no consumers)
- Phase 2: `make test` passes (all engines migrated to TranscriberParams)
- Phase 3: `make test` passes (all renames, pure refactor)
- Phase 4: `make test` passes (RivaCore extraction, pure refactor)
