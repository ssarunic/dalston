# M51 Engine Testing Playbook

This document explains how engine testing changed after M51 (`process(input, ctx)` + artifact materialization).

## What Changed

Before M51, many tests verified URI-based behavior (`audio_uri`, S3 helper usage inside engines).

After M51, tests should verify:

1. Engines are stateless compute units:
   - `def process(self, input: EngineInput, ctx: BatchTaskContext) -> EngineOutput`
2. Engines consume local materialized files:
   - `input.audio_path` / `input.materialized_artifacts`
3. Engines declare outputs as produced artifacts:
   - `EngineOutput(..., produced_artifacts=[...])`
4. Orchestrator/scheduler resolve dependencies via artifact selectors/bindings:
   - `InputBinding` + `ArtifactSelector`
5. Runtime infra (runner/materializer), not engines, handles storage I/O.

## Canonical M51 Test Layers

| Layer | Purpose | Main tests |
| --- | --- | --- |
| Contract model tests | Validate URI-free schemas and artifact contracts | `tests/unit/test_m51_contract_models.py` |
| Materializer tests | Validate download/upload + artifact reference generation | `tests/unit/test_m51_materializer.py` |
| DAG/binding tests | Validate selector-based task wiring and channel-aware bindings | `tests/unit/test_m51_orchestrator_bindings.py` |
| Engine artifact tests | Verify stage engines declare produced artifacts correctly | `tests/unit/test_m51_engine_artifacts.py` |
| Local runner tests | Verify no-Redis/no-S3 execution path | `tests/unit/test_m51_local_runner.py` |
| Local runner CLI tests (M52) | Verify file-based command contract + output envelope | `tests/unit/test_m52_local_runner_cli.py`, `tests/unit/test_m52_local_runner_contract.py` |
| SDK hardening tests (M52) | Enforce no alias exports + fail-closed typed parsing + engine-ID stream polling | `tests/unit/test_m52_sdk_surface.py`, `tests/unit/test_m52_engine_input_contract.py`, `tests/unit/test_m52_runner_stream_contract.py` |
| Realtime side-effect boundary tests | Verify `SessionStorage` adapter behavior | `tests/unit/test_m51_realtime_storage.py` |
| Static guardrails | Enforce signature + no URI/storage coupling in engines | `tests/unit/test_m51_enforcement.py` |

## Stage-By-Stage Testing Checklist

| Stage | Input expectation | Output expectation | Must-test change |
| --- | --- | --- | --- |
| `prepare` | Source audio materialized as slot `audio` | `PrepareOutput.channel_files[*].artifact_id` + produced audio artifacts | No URI fields in output media |
| `transcribe` | Prepared audio from `prepare` via local path | `TranscribeOutput` typed payload | No direct storage clients/helpers in `process` |
| `align` | Prepared audio + previous transcribe output | `AlignOutput` typed payload | Uses stage outputs, not URI conventions |
| `diarize` | Prepared audio via artifact binding | `DiarizeOutput` typed payload | Channel-aware behavior still works where needed |
| `pii_detect` | Primarily `previous_outputs` (transcribe/align/diarize) | `PIIDetectOutput` typed payload | No hidden dependency on audio URIs |
| `audio_redact` | Audio slot + PII stage outputs | `AudioRedactOutput.redacted_audio_artifact_id` + produced redacted audio artifact | Renamed field from `redacted_audio_uri` |
| `merge` | Stage outputs + optional redacted audio materialized slots | `MergeOutput` + produced transcript artifact (`kind=transcript`) | Merge writes transcript artifact declaration, not storage URI |

Per-channel mode (`speaker_detection=per_channel`) must additionally verify:

1. `transcribe_chN` / `align_chN` / `pii_detect_chN` / `audio_redact_chN` tasks use channel selectors.
2. Merge bindings include channel-specific slots such as `redacted_audio_ch0`.

## Minimal Unit Test Pattern (Engine)

```python
from pathlib import Path

from dalston.common.artifacts import MaterializedArtifact
from dalston.engine_sdk.context import BatchTaskContext
from dalston.engine_sdk.types import EngineInput


def test_engine_process_contract(tmp_path: Path) -> None:
    audio = tmp_path / "in.wav"
    audio.write_bytes(b"audio")

    task_input = EngineInput(
        task_id="task-1",
        job_id="job-1",
        stage="transcribe",
        materialized_artifacts={
            "audio": MaterializedArtifact(
                artifact_id="task-prepare:prepared_audio",
                kind="audio",
                local_path=audio,
            )
        },
        config={},
    )
    ctx = BatchTaskContext(
        engine_id="test-engine_id",
        instance="test-instance",
        task_id="task-1",
        job_id="job-1",
        stage="transcribe",
    )

    output = engine.process(task_input, ctx)

    assert output.data is not None
    # For stages that generate files, also assert output.produced_artifacts
```

## Guardrails You Should Always Run

```bash
pytest tests/unit/test_m51_enforcement.py -q
```

When changing engine contracts or artifact flow:

```bash
pytest \
  tests/unit/test_m51_contract_models.py \
  tests/unit/test_m51_materializer.py \
  tests/unit/test_m51_orchestrator_bindings.py \
  tests/unit/test_m51_engine_artifacts.py \
  tests/unit/test_m51_local_runner.py \
  tests/unit/test_m52_local_runner_cli.py \
  tests/unit/test_m52_local_runner_contract.py \
  tests/unit/test_m52_sdk_surface.py \
  tests/unit/test_m52_engine_input_contract.py \
  tests/unit/test_m52_runner_stream_contract.py \
  tests/unit/test_m51_realtime_storage.py \
  tests/unit/test_m51_enforcement.py -q
```

When changing stage outputs or pipeline schemas:

```bash
pytest \
  tests/unit/test_pipeline_types.py \
  tests/unit/test_engine_sdk_types.py \
  tests/integration/test_engine_typed_outputs.py -q
```

## Common Failure Modes (Post-M51)

1. Engine imports `dalston.engine_sdk.io` / `boto3` / `redis` directly.
2. Engine emits or parses `s3://...` literals inside `process`.
3. Stage output still uses old URI field names (`redacted_audio_uri`, `AudioMedia.uri`).
4. Engine creates files but forgets to declare them in `produced_artifacts`.
5. DAG lacks correct `input_bindings`, causing missing materialized slots at runtime.
6. Tests still import deprecated `TaskInput` / `TaskOutput` aliases instead of `EngineInput` / `EngineOutput`.
