# M55: Non-Transcribe Runtime Model Management - Implementation Report

## Scope

Implemented runtime-model selection and propagation for non-transcribe model-backed stages:

- `diarize` (`model_diarize`)
- `align` (`model_align`)
- `pii_detect` (`model_pii_detect`)

## Delivered Changes

1. Control plane contract

- Added stage model request parameters in `POST /v1/audio/transcriptions`.
- Selector now supports deterministic stage-model errors:
  - `model_not_found`
  - `model_stage_mismatch`
  - `model_not_ready`
  - `runtime_unavailable`
- Non-transcribe stage selection now uses stage model parameters instead of legacy `engine_*` stage overrides.

2. Orchestrator/DAG propagation

- DAG now propagates `runtime_model_id` per stage, not only for transcribe.
- `runtime_model_id` is injected for:
  - standard: `diarize`, `align`, `pii_detect`
  - per-channel: `align_ch*`, `pii_detect_ch*`

3. Engine refactors

- `pyannote-4.0`: consumes `input.config["runtime_model_id"]`, caches by model ID, reports active model.
- `nemo-msdd`: consumes `runtime_model_id`, resolves component model set by runtime model, reports active model.
- `phoneme-align`: consumes `runtime_model_id`, caches by model ID, reports cached models.
- `pii-presidio`: consumes `runtime_model_id` for GLiNER backbone, caches by model ID, reports active model.

4. Registry lifecycle updates

- Model in-use checks now include stage model parameters:
  - `model_diarize`
  - `model_align`
  - `model_pii_detect`
  - plus transcribe keys.

5. Registry seed data

- Added non-transcribe model YAML entries in `models/` for diarize, align, and pii detection runtimes.

6. Specs/docs updates

- Updated model selection and pipeline interface docs to include stage model contract and stage runtime model propagation.

## Behavior Changes

1. Stage model ID is now validated against registry stage and status before task creation.
2. Non-transcribe engines no longer rely on hardcoded runtime model defaults in task processing paths.
3. Missing or invalid stage model selection fails deterministically with explicit model-selection error codes.

## Test Evidence

Executed during implementation:

1. `tests/unit/test_engine_selector.py` + `tests/unit/test_model_registry_service.py` + `tests/unit/test_pii_detection.py`
2. `tests/unit/test_dag.py` + `tests/unit/test_parakeet_engine.py` + `tests/unit/test_parakeet_onnx_engine.py`
3. `tests/unit/test_phoneme_align_engine.py` + `tests/unit/test_pii_detection_engine.py`
4. `tests/integration/test_model_endpoints_auth.py`

All listed test runs passed in the implementation branch environment (`.venv`).
