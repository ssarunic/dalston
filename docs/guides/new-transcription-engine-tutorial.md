# Tutorial: Add a New Transcription Engine (Beginner-Friendly)

This guide shows the simplest path to add a new batch transcription engine after M51.

## 0. What "Done" Looks Like

A new engine is considered integrated when:

1. It implements `process(input, ctx)` with the M51 contract.
2. It returns typed `TranscribeOutput`.
3. It does not do storage I/O in engine business logic.
4. It passes unit/integration + M51 guardrail tests.

## 1. Scaffold The Engine

Generate a starter engine:

```bash
python -m dalston.tools.scaffold_engine my-asr --stage transcribe
```

This creates files under:

`engines/stt-transcribe/my-asr/`

Main files:

1. `engine.py`
2. `engine.yaml`
3. `requirements.txt`
4. `Dockerfile`

Tip: list valid stages with:

```bash
python -m dalston.tools.scaffold_engine --list-stages
```

## 2. Configure `engine.yaml`

Set realistic capabilities so routing can choose your engine correctly.

At minimum verify:

1. `stage: transcribe`
2. `engine_id`/`id` naming consistency
3. `capabilities.languages`
4. `capabilities.word_timestamps`
5. `container.gpu` and hardware section

Example skeleton:

```yaml
schema_version: "1.1"
id: my-asr
engine_id: my-asr
stage: transcribe
name: My ASR Runtime
version: 1.0.0

container:
  gpu: optional
  memory: 8G
  model_cache: /models

capabilities:
  languages:
    - all
  max_audio_duration: 7200
  streaming: false
  word_timestamps: true
  includes_diarization: false
```

## 3. Implement `process(input, ctx)`

The core rule after M51:

1. Use local files (`input.audio_path`), not URIs.
2. Do not call S3/Redis helpers in `process`.
3. Return typed output.

Minimal transcription engine shape:

```python
from dalston.engine_sdk import (
    AlignmentMethod,
    BatchTaskContext,
    Engine,
    EngineRequest,
    EngineResponse,
    Segment,
    TimestampGranularity,
    TranscribeOutput,
    Word,
)


class MyAsrEngine(Engine):
    def __init__(self) -> None:
        super().__init__()
        self._model = None

    def _load_model(self, config: dict) -> None:
        if self._model is None:
            # load your engine ID model here
            self._model = object()

    def process(self, input: EngineRequest, ctx: BatchTaskContext) -> EngineResponse:
        self._load_model(input.config)

        audio_path = input.audio_path
        language = input.config.get("language") or "en"

        # replace this with real inference
        segments = [
            Segment(
                start=0.0,
                end=1.0,
                text="hello world",
                words=[
                    Word(
                        text="hello",
                        start=0.0,
                        end=0.5,
                        confidence=0.9,
                        alignment_method=AlignmentMethod.ATTENTION,
                    ),
                    Word(
                        text="world",
                        start=0.5,
                        end=1.0,
                        confidence=0.9,
                        alignment_method=AlignmentMethod.ATTENTION,
                    ),
                ],
            )
        ]

        out = TranscribeOutput(
            text="hello world",
            segments=segments,
            language=language,
            engine_id="my-asr",
            timestamp_granularity_requested=TimestampGranularity.WORD,
            timestamp_granularity_actual=TimestampGranularity.WORD,
            alignment_method=AlignmentMethod.ATTENTION,
        )
        return EngineResponse(data=out)
```

Notes:

1. Transcribe engines usually return no `produced_artifacts` (payload-only output).
2. Use `ctx` for metadata/logging context only.

### 3.1 Handling audio duration limits (optional)

If your engine has a per-request audio duration ceiling — either a hard
model cap (audio LLMs like Gemma 4 E4B with a 30s encoder limit) or a
VRAM-driven soft ceiling (e.g. NeMo Parakeet on L4, which linearly grows
activation beyond ~100 minutes) — override
`get_max_audio_duration_s(task_request)` in your engine class. When the
input audio exceeds that limit, the base engine auto-chunks via Silero
VAD, runs `transcribe_audio()` per chunk, and merges results
transparently. The chunked path also has OOM backoff and aggregate
telemetry, so you get safety and observability for free.

```python
class MyAsrEngine(BaseBatchTranscribeEngine):
    def get_max_audio_duration_s(self, task_request):
        return 1500  # per-chunk ceiling in seconds
```

Return `None` (or omit the override — that's the default) for engines
that handle any length natively (HF-ASR, faster-whisper, ONNX, and the
majority of transcribe engines). See
[M86](../plan/milestones/M86-shared-vad-chunking.md) for the full
chunking contract.

## 4. Write Tests First (Recommended Minimal Set)

Create a focused unit test for your new engine:

```python
from pathlib import Path

from dalston.common.artifacts import MaterializedArtifact
from dalston.engine_sdk.context import BatchTaskContext
from dalston.engine_sdk.types import EngineRequest


def test_my_asr_process_returns_transcribe_output(tmp_path: Path) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fake")

    task_input = EngineRequest(
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
        engine_id="my-asr",
        instance="test",
        task_id="task-1",
        job_id="job-1",
        stage="transcribe",
    )

    output = engine.process(task_input, ctx)
    assert output.data.engine_id == "my-asr"
    assert output.data.text
```

## 5. Quick Local Execution Without Redis/S3

Use the M52 file-based local runner command as the default developer loop:

```bash
python -m dalston.engine_sdk.local_runner run \
  --engine engines.stt-transcribe.faster-whisper.engine:FasterWhisperEngine \
  --stage transcribe \
  --audio ./fixtures/audio.wav \
  --config ./fixtures/transcribe-config.json \
  --output ./tmp/response.json
```

Advanced stages can include optional JSON inputs:

```bash
python -m dalston.engine_sdk.local_runner run \
  --engine engines.stt-align.phoneme-align.engine:PhonemeAlignEngine \
  --stage align \
  --config ./fixtures/align-config.json \
  --payload ./fixtures/align-payload.json \
  --previous-outputs ./fixtures/previous-outputs.json \
  --artifacts ./fixtures/artifacts.json \
  --output ./tmp/response.json
```

`response.json` always uses the canonical envelope:

```json
{
  "task_id": "task-local",
  "job_id": "job-local",
  "stage": "transcribe",
  "data": {},
  "produced_artifacts": [],
  "produced_artifact_ids": []
}
```

## 6. Validation Commands

Run your engine tests + M51/M52 guardrails:

```bash
pytest tests/unit/test_m51_enforcement.py -q
pytest tests/unit/test_engine_capabilities.py -q
pytest tests/unit/test_engine_sdk_types.py -q
pytest tests/unit/test_m52_local_runner_cli.py -q
pytest tests/unit/test_m52_engine_input_contract.py -q
pytest tests/integration/test_engine_typed_outputs.py -q
```

If you added new stage-specific tests, include them in the same run.

## 7. Common Mistakes

1. Importing `dalston.engine_sdk.io` (or `boto3`/`redis`) in engine code.
2. Building/parsing `s3://...` paths in `process`.
3. Returning plain dict output when typed `TranscribeOutput` is expected.
4. Forgetting language/timestamp capability alignment between `engine.py` and `engine.yaml`.
5. Skipping `test_m51_enforcement.py` after engine edits.

## 8. If You Also Need Deployment Wiring

If this engine should run in the stack, add a service in compose by copying an existing transcribe engine service and updating:

1. build context to `engines/stt-transcribe/my-asr`
2. `DALSTON_ENGINE_ID=my-asr`
3. any engine-specific environment variables

No orchestrator code change is required for normal capability-based selection.
