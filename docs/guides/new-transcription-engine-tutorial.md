# Tutorial: add a transcription engine

This guide builds a batch transcription engine using the current typed engine
contract. A complete engine:

1. receives a local audio path in `TaskRequest`;
2. returns a canonical `Transcript` inside `TaskResponse`;
3. performs no Redis, S3, or other storage I/O in inference code;
4. declares capabilities that agree with its output; and
5. passes the engine contract tests.

## 1. Scaffold the files

The scaffold command is a dry run unless you pass `--no-dry-run`:

```bash
python -m dalston.tools.scaffold_engine my-asr \
  --stage transcribe \
  --no-dry-run
```

This creates `engine.py`, `engine.yaml`, `requirements.txt`, `Dockerfile`, and
`README.md` under `engines/stt-transcribe/my-asr/`.

List accepted stages with:

```bash
python -m dalston.tools.scaffold_engine --list-stages
```

The generated `engine.py` is a generic starting point. For transcription,
replace it with a `BaseBatchTranscribeEngine` adapter as shown below.

## 2. Declare capabilities

Review the generated `engine.yaml`. The important fields are:

```yaml
schema_version: "1.1"
id: my-asr
stage: transcribe
name: My ASR
version: 1.0.0

container:
  gpu: optional
  memory: 8G
  model_cache: /models

capabilities:
  languages:
    - all
  max_audio_duration: 7200
  native_streaming: false
  word_timestamps: true
  includes_diarization: false
  language_forcing: true
```

Do not advertise word timestamps, native streaming, language forcing, or
speaker labels unless the engine really produces them. Capability-driven
routing uses these values to construct the DAG.

## 3. Implement inference

`BaseBatchTranscribeEngine` owns the `process()` envelope and optional
long-audio chunking. Implement `transcribe_audio()` and return a `Transcript`:

```python
from dalston.engine_sdk import (
    AlignmentMethod,
    BaseBatchTranscribeEngine,
    BatchTaskContext,
    TaskRequest,
    Transcript,
)


class MyAsrEngine(BaseBatchTranscribeEngine):
    ENGINE_ID = "my-asr"

    def __init__(self) -> None:
        super().__init__()
        self._model = None

    def _load_model(self, model_id: str | None) -> None:
        if self._model is None:
            # Replace with the library/model initialization.
            self._model = object()

    def transcribe_audio(
        self,
        task_request: TaskRequest,
        ctx: BatchTaskContext,
    ) -> Transcript:
        params = task_request.get_transcribe_params()
        self._load_model(params.loaded_model_id)

        if task_request.audio_path is None:
            raise ValueError("transcription requires an audio artifact")

        # Replace this block with inference against task_request.audio_path.
        words = [
            self.build_word(
                text="hello",
                start=0.0,
                end=0.5,
                confidence=0.9,
                alignment_method=AlignmentMethod.ATTENTION,
            ),
            self.build_word(
                text=" world",
                start=0.5,
                end=1.0,
                confidence=0.9,
                alignment_method=AlignmentMethod.ATTENTION,
            ),
        ]
        segments = [
            self.build_segment(
                start=0.0,
                end=1.0,
                text="hello world",
                words=words,
                language=params.language,
                confidence=0.9,
            )
        ]

        language = (
            params.language
            if params.language and params.language != "auto"
            else "und"
        )
        return self.build_transcript(
            text="hello world",
            segments=segments,
            language=language,
            language_source="requested" if language != "und" else None,
            engine_id=self.engine_id,
            duration=1.0,
            alignment_method=AlignmentMethod.ATTENTION,
            words_expected=True,
        )


if __name__ == "__main__":
    MyAsrEngine().run()
```

Use `ctx.logger` or `self.logger` for structured logging. Engine inference code
must not download from object storage, publish Redis events, or construct
`s3://` paths; the runner materializes input artifacts and persists declared
outputs.

### Optional long-audio chunking

Override `get_max_audio_duration_s()` only when the model has a hard or
VRAM-driven request limit:

```python
class MyAsrEngine(BaseBatchTranscribeEngine):
    def get_max_audio_duration_s(self, task_request: TaskRequest) -> float | None:
        return 1500
```

When audio exceeds the limit, the base class splits it at VAD boundaries,
retries smaller chunks after CUDA OOM, and offsets/combines the resulting
transcripts. Return `None`, the default, when the runtime handles arbitrary
lengths itself.

## 4. Add a contract test

```python
from pathlib import Path

from dalston.engine_sdk import BatchTaskContext, TaskRequest, Transcript

from engine import MyAsrEngine


def test_my_asr_returns_transcript(tmp_path: Path) -> None:
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"fixture")

    request = TaskRequest(
        task_id="task-1",
        job_id="job-1",
        stage="transcribe",
        audio_path=audio,
        config={"language": "en", "word_timestamps": True},
    )
    context = BatchTaskContext(
        engine_id="my-asr",
        instance="test",
        task_id="task-1",
        job_id="job-1",
        stage="transcribe",
    )

    response = MyAsrEngine().process(request, context)
    assert isinstance(response.data, Transcript)
    assert response.data.engine_id == "my-asr"
    assert response.data.text == "hello world"
    assert response.data.timestamp_granularity.value == "word"
```

Use a real decodable audio fixture once inference is connected; the byte stub
is suitable only for the placeholder implementation above.

## 5. Run it without Redis or S3

Create a JSON configuration:

```json
{
  "language": "en",
  "word_timestamps": true
}
```

Then invoke the local filesystem runner:

```bash
python -m dalston.engine_sdk.local_runner run \
  --engine engines/stt-transcribe/my-asr/engine.py:MyAsrEngine \
  --stage transcribe \
  --audio ./fixtures/audio.wav \
  --config ./fixtures/transcribe-config.json \
  --output ./tmp/response.json
```

For stages that consume earlier task results, use `--previous-responses`:

```bash
python -m dalston.engine_sdk.local_runner run \
  --engine engines/stt-align/phoneme-align/engine.py:PhonemeAlignEngine \
  --stage align \
  --config ./fixtures/align-config.json \
  --payload ./fixtures/align-payload.json \
  --previous-responses ./fixtures/previous-responses.json \
  --artifacts ./fixtures/artifacts.json \
  --output ./tmp/response.json
```

The output uses the canonical task envelope:

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

## 6. Validate

Run focused tests before the full suite:

```bash
pytest tests/unit/test_m51_enforcement.py -q
pytest tests/unit/test_engine_capabilities.py -q
pytest tests/unit/test_engine_sdk_types.py -q
pytest tests/unit/test_m52_local_runner_cli.py -q
pytest tests/unit/test_m52_engine_input_contract.py -q
pytest tests/integration/test_engine_typed_outputs.py -q
```

Common integration mistakes are capability/output disagreement, returning a
plain legacy output shape instead of `Transcript`, importing storage clients in
engine code, and using `--previous-outputs` instead of
`--previous-responses`.

## 7. Add deployment wiring

For a distributed worker, copy an existing transcription service in
`docker-compose.yml` and update its build/image, `DALSTON_ENGINE_ID`, model
configuration, and resource settings. Capability-driven selection normally
requires no orchestrator code change.
