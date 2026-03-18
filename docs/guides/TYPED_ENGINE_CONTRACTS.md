# Typed Engine Contracts

Every pipeline stage has a typed config model (`StageInput` subclass) that
validates the config dict passed from the orchestrator to the engine via S3
`request.json`. This replaces raw `config.get("field")` calls with
Pydantic-validated typed access.

## How it works

```
Orchestrator (dag.py)          Scheduler (scheduler.py)         Engine
─────────────────────          ────────────────────────         ──────
builds config dict     →       validates against                deserializes
for each task                  STAGE_CONFIG_MAP[stage]          via get_*_params()
                               before writing to S3             (Pydantic validated)
```

All three layers use the **same Pydantic model**. If the orchestrator
constructs a bad config, the scheduler catches it before the task even
reaches the engine.

## Stage config models

| Stage          | Model                  | Location                               |
|----------------|------------------------|-----------------------------------------|
| `prepare`      | `PreparationRequest`   | `dalston/common/pipeline_types.py`      |
| `transcribe`   | `TranscriptionRequest` | `dalston/common/pipeline_types.py`      |
| `align`        | `AlignmentRequest`     | `dalston/common/pipeline_types.py`      |
| `diarize`      | `DiarizationRequest`   | `dalston/common/pipeline_types.py`      |
| `pii_detect`   | `PIIDetectionRequest`  | `dalston/common/pipeline_types.py`      |
| `audio_redact` | `AudioRedactRequest`   | `dalston/common/pipeline_types.py`      |
| `merge`        | `MergeRequest`         | `dalston/common/pipeline_types.py`      |

The full map lives in `STAGE_CONFIG_MAP` in `pipeline_types.py`.

## Writing a new engine

1. **Define your stage's config model** in `pipeline_types.py`:

```python
class MyStageRequest(StageInput):
    """Input for my_stage."""
    loaded_model_id: str | None = Field(default=None)
    my_param: int = Field(default=42, description="...")
```

2. **Register it** in `STAGE_CONFIG_MAP`:

```python
STAGE_CONFIG_MAP: dict[str, type[StageInput]] = {
    ...
    "my_stage": MyStageRequest,
}
```

3. **Use typed params in your engine**:

```python
def process(self, task_request: TaskRequest, ctx: BatchTaskContext) -> TaskResponse:
    params = task_request.get_stage_config()  # returns MyStageRequest
    # or add a dedicated accessor:
    # params = MyStageRequest.model_validate(task_request.config)
    model_id = params.loaded_model_id
    my_param = params.my_param
```

4. **Build config in the DAG builder** (`dag.py` or `post_processor.py`):

```python
config = {"loaded_model_id": "my-model", "my_param": 100}
task = Task(..., stage="my_stage", config=config)
```

The scheduler will validate `config` against `MyStageRequest` before
writing to S3. Bad configs fail at dispatch time, not at engine
processing time.

## Forward compatibility

All `StageInput` models use `extra="ignore"` with a warning logger.
This means:

- **New optional fields with defaults** — safe during rolling deploys.
  Old engines ignore the new field. New engines use the default if the
  field is absent.
- **Unknown fields** — logged as warnings but not rejected. This
  covers the transition period when orchestrator sends fields the
  engine doesn't know about yet.
- **Removing fields** — safe. Old engines that send the removed field
  get a warning; new engines don't see it.
- **Renaming or changing types** — breaking. Deploy engines first,
  orchestrator second.

## Schema version

`PIPELINE_SCHEMA_VERSION` in `pipeline_types.py` is a string that
should be bumped when you change any model in that file. It is:

- Logged at engine startup (`pipeline_schema_version=...`)
- Logged at orchestrator startup
- Included in engine heartbeat registration (`schema_version` field)

If you see a stale-container bug, check:

```bash
docker compose logs | grep pipeline_schema_version
```

All containers should show the same version. If they don't, rebuild
the base image:

```bash
docker build -f docker/Dockerfile.engine-base -t dalston/engine-base:latest .
docker compose build --no-cache <service>
```
