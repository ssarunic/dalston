# M73: Unified Transcriber Input + Runtime-Based Engine Naming

| | |
|---|---|
| **Goal** | Standardize transcriber input typing and engine/core naming across batch + realtime |
| **Duration** | 1-2 weeks (incremental, test-gated) |
| **Dependencies** | M43, M44, M52, M63 |
| **Primary Deliverable** | Runtime-based class names and shared typed transcriber params (`TranscribeInput`) |
| **Status** | Complete |

## Background

Current transcription adapters diverged in two ways:

1. Input handling: most engines parse `engine_input.config` ad hoc with repeated
   `config.get(...)` logic and slightly different defaults.
2. Naming: engine/core class names mix architecture labels (`Parakeet`) and
   inconsistent mode suffixes (`Streaming`, missing `Batch`).

M73 standardizes both:

- one canonical typed transcribe input (`TranscribeInput`) across batch + RT;
- class names by runtime + mode (`Batch`/`Realtime`) with no compatibility aliases.

## Outcomes

1. Transcribe engines consume one typed params model (`TranscribeInput`) instead of ad-hoc config parsing.
2. Engine and core class names are consistent by runtime and mode (`Batch`/`Realtime`).
3. No behavior regressions in batch/realtime API contracts.
4. Runtime identifiers in `engine.yaml` remain unchanged.

## Completion Notes (2026-03-11)

Delivered:

- Runtime-based class naming refactor across batch + realtime transcribe engines.
- Canonical typed transcribe params wiring via `TranscribeInput`.
- Batch engines migrated to `EngineInput.get_transcribe_params()`.
- Realtime engine signatures migrated to typed params model.
- Unit/integration coverage updated for renamed classes and typed params flow.

Deferred to follow-up iteration:

- Voxtral runtime consolidation beyond naming/typed-params scope.
- Broader Voxtral realtime behavior work (audio-state continuity and timestamp
  parity improvements on vLLM path).

## Scope

In scope:

- Rename core + engine classes to runtime-based naming.
- Update all internal references (imports, runners, tests, defaults) in the same rollout.
- Extend `TranscribeInput` with fields required by current transcribe engines.
- Wire typed params through batch and realtime SDKs.

Out of scope for this milestone:

- Runtime consolidation or engine deletion (for example Voxtral absorption).
- Runtime string changes.
- Directory renames.
- Backward-compatibility aliases for renamed class names.

## Strategy

1. Mechanical naming refactor first (no behavior change).
2. Add typed transcribe params accessors in SDK layers.
3. Migrate batch transcribe engines to typed params.
4. Migrate realtime transcribe callback/signatures to typed params.
5. Keep rollout test-gated at each phase.

## Tactics

### T1. Core + Engine Class Renames

Apply runtime-based names:

- Core:
  - `TranscribeCore` -> `FasterWhisperCore`
  - `TranscribeConfig` -> `FasterWhisperConfig`
  - `ParakeetCore` -> `NemoCore`
  - `ParakeetOnnxCore` -> `NemoOnnxCore`
- Batch:
  - `WhisperEngine` -> `FasterWhisperBatchEngine`
  - `ParakeetEngine` -> `NemoBatchEngine`
  - `ParakeetOnnxEngine` -> `NemoOnnxBatchEngine`
  - `HFASREngine` -> `HfAsrBatchEngine`
  - `VLLMASREngine` -> `VllmAsrBatchEngine`
  - `VoxtralEngine` -> `VoxtralBatchEngine`
- Realtime:
  - `WhisperStreamingEngine` -> `FasterWhisperRealtimeEngine`
  - `ParakeetStreamingEngine` -> `NemoRealtimeEngine`
  - `ParakeetOnnxStreamingEngine` -> `NemoOnnxRealtimeEngine`
  - `VoxtralStreamingEngine` -> `VoxtralRealtimeEngine`

Gate:

- All imports/runners/tests/default refs updated in the same commit.
- `make test` passes.

### T2. Canonical Typed Transcriber Params

Use `dalston.common.pipeline_types.TranscribeInput` as canonical params model.

Add fields required by current transcribe paths:

- `runtime_model_id: str | None`
- `channel: int | None`
- `word_timestamps: bool | None`

Keep `runtime_model_id` as canonical model-selection key.

Gate:

- New/updated unit tests for typed parsing and defaults pass.

### T3. Batch SDK + Engine Migration

- Add typed transcribe params accessor on `EngineInput`.
- Migrate transcribe batch engines to typed params:
  - faster-whisper
  - nemo
  - nemo-onnx
  - hf-asr
  - vllm-asr
  - riva
  - voxtral

Gate:

- Batch transcribe contract tests pass.

### T4. Realtime SDK + Engine Migration

- Change realtime transcribe callback/engine signatures to:
  - `(audio: np.ndarray, params: TranscribeInput) -> Transcript`
- Build params from live session config at call time.
- Migrate realtime transcribe engines:
  - faster-whisper
  - nemo
  - nemo-onnx
  - riva
  - voxtral

Gate:

- Realtime protocol/session contract tests pass.

## Implementation Plan

### Phase 1: Naming Standardization

1. Apply class renames.
2. Update unified runners, lite defaults, and tests.
3. Validate no behavior diffs beyond naming.

### Phase 2: Typed Params Model + SDK Wiring

1. Extend `TranscribeInput`.
2. Add typed accessor on `EngineInput`.
3. Wire batch and realtime SDK plumbing.

### Phase 3: Engine Migrations

1. Batch transcribe engines switch to typed params.
2. Realtime transcribe engines switch to typed params signatures.
3. Verify all contract tests.

## Testing Matrix

Required per phase:

- Unit: engine SDK types, core/engine contract tests, runner imports.
- Integration: realtime protocol + batch transcription flows.
- Regression: lite pipeline default transcribe engine binding.

Command gate:

```bash
make test
```

Optional targeted sweeps during development:

```bash
pytest tests/unit/test_engine_sdk_types.py
pytest tests/unit/test_runtime_executor_contract.py
pytest tests/unit/test_faster_whisper_batch_contract.py
pytest tests/unit/test_faster_whisper_rt_contract.py
```

## Success Criteria

- All transcribe engines use typed params access instead of direct `config.get(...)`.
- Class naming is consistent by runtime and mode.
- No root planning document; milestone lives under `docs/plan/milestones/`.
- Test suite passes without compatibility aliases.

## References

- `dalston/common/pipeline_types.py`
- `dalston/engine_sdk/types.py`
- `dalston/realtime_sdk/base.py`
- `dalston/realtime_sdk/base_transcribe.py`
- `dalston/realtime_sdk/session.py`
- `engines/stt-transcribe/*/engine.py`
- `engines/stt-rt/*/engine.py`
- `engines/stt-unified/*/runner.py`
