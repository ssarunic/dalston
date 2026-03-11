# Dalston Engines Reference

## Overview

Engines are containerized processors that implement one or more pipeline stages. Each engine runs in its own Docker container with isolated dependencies and can load multiple model variants at runtime.

---

## Engine Architecture

### Runtime-Based Design (M36)

Dalston uses a **runtime-based architecture** where a small number of engine runtimes can load any compatible model on demand:

```
┌─────────────────────────────────────────────────────────┐
│                    Engine Runtime                        │
│  (e.g., faster-whisper, nemo, parakeet-onnx)           │
├─────────────────────────────────────────────────────────┤
│  ModelManager                                            │
│  ├── TTL-based eviction (default: 1 hour)               │
│  ├── LRU eviction when at capacity                      │
│  ├── Reference counting (no eviction during use)        │
│  └── GPU memory cleanup on model swap                   │
├─────────────────────────────────────────────────────────┤
│  S3ModelStorage                                          │
│  ├── Download from S3 to local cache                    │
│  ├── .complete marker for atomic availability           │
│  └── Runtime-specific cache directories                 │
└─────────────────────────────────────────────────────────┘
```

### Model Loading Flow

```
Job arrives with runtime_model_id
      ↓
Check if model loaded in memory
      ↓
┌─────────────────────────────────────┐
│ Model loaded?                        │
│   YES → Use immediately              │
│   NO  → Check local cache            │
│         ├── Cached? → Load to GPU    │
│         └── No? → Download from S3   │
│                   → Load to GPU      │
└─────────────────────────────────────┘
      ↓
Process audio
      ↓
Release model reference (available for eviction)
```

---

## Engine Categories

### PREPARE

Audio preprocessing and analysis.

| Engine ID | Description | GPU |
|-----------|-------------|-----|
| `audio-prepare` | Analyze, convert, resample audio to 16kHz/16-bit | No |
| `channel-splitter` | Split multichannel into separate mono files | No |
| `vad-chunker` | Split long audio at silence points using VAD | No |

### TRANSCRIBE

Speech-to-text conversion. These are **runtime-based engines** that load models dynamically.

| Runtime | Library | Models | Languages | GPU |
|---------|---------|--------|-----------|-----|
| `faster-whisper` | CTranslate2 | Whisper variants | 99 | Optional |
| `nemo` | NeMo | Parakeet CTC/TDT/RNNT | English | Optional |
| `parakeet-onnx` | ONNX Runtime | Parakeet ONNX | English | Optional |
| `hf-asr` | Transformers | Any HF ASR model | Varies | Varies |
| `vllm-asr` | vLLM | Voxtral, Qwen2-Audio | 13+ | Yes |

### ALIGN

Word-level timestamp alignment.

| Engine ID | Description | GPU |
|-----------|-------------|-----|
| `phoneme-align` | Standalone CTC forced alignment (wav2vec2-based) | Optional |

### DIARIZE

Speaker identification and segmentation.

| Engine ID | Description | GPU |
|-----------|-------------|-----|
| `pyannote-4.0` | Pyannote 4.0, latest features | Yes |
| `nemo-diarizer` | NVIDIA NeMo diarization | Yes |
| `speechbrain-diar` | SpeechBrain diarization | Yes |

### DETECT

Audio analysis and classification.

| Engine ID | Description | GPU |
|-----------|-------------|-----|
| `emotion2vec` | Emotion detection from speech | Yes |
| `panns-events` | Audio event detection (laughter, music, etc.) | No |
| `topic-classifier` | Topic/category classification | No |
| `pii-detect` | PII detection in transcripts | No |

### REDACT

Audio redaction based on detected PII.

| Engine ID | Description | GPU |
|-----------|-------------|-----|
| `audio-redact` | Replace PII segments with silence/beep | No |

### REFINE

LLM-based transcript refinement.

| Engine ID | Description | GPU |
|-----------|-------------|-----|
| `llm-cleanup` | Error correction, speaker naming, punctuation | No |

### MERGE

Combine outputs from multiple stages.

| Engine ID | Description | GPU |
|-----------|-------------|-----|
| `transcript-merger` | Merge transcription + diarization + alignment | No |
| `channel-merger` | Merge parallel channel transcriptions | No |
| `final-merger` | Combine all results into final output | No |

---

## Engine Metadata Format

Each engine has an `engine.yaml` file (schema version 1.1) describing its capabilities.

### Runtime Engine Schema

For transcription runtimes that load models dynamically:

```yaml
# Runtime-level engine.yaml (e.g., engines/stt-transcribe/faster-whisper/engine.yaml)
schema_version: "1.1"
id: faster-whisper                      # Runtime ID
runtime: faster-whisper                 # Runtime family
stage: transcribe
name: Faster Whisper Runtime
version: 1.0.0
description: |
  CTranslate2-optimized Whisper implementation.
  Loads any Whisper model variant at runtime.

execution_profile: container              # inproc | venv | container

container:
  gpu: optional                         # required | optional | none
  memory: 8G
  model_cache: /models                  # Model cache directory

capabilities:
  languages: null                       # null = multilingual (all)
  max_audio_duration: 7200
  streaming: false
  word_timestamps: false                # Via alignment stage
  includes_diarization: false

# HuggingFace ecosystem compatibility
hf_compat:
  pipeline_tag: automatic-speech-recognition
  library_name: ctranslate2
  license: mit

# Hardware requirements (minimum for any model)
hardware:
  min_vram_gb: 4
  recommended_gpu:
    - t4
    - a10g
  supports_cpu: true
  min_ram_gb: 8

# Performance (varies by model)
performance:
  rtf_gpu: 0.05
  rtf_cpu: 0.8
  warm_start_latency_ms: 50
```

### Utility Engine Schema

For single-purpose utility engines:

```yaml
# Utility engine.yaml (e.g., engines/stt-merge/final-merger/engine.yaml)
schema_version: "1.1"
id: final-merger
runtime: final-merger                   # Same as ID for utilities
stage: merge
name: Final Merger
version: 1.0.0
description: Merges all pipeline stage outputs into final transcript.

execution_profile: container

container:
  gpu: none
  memory: 2G

capabilities:
  streaming: false

hardware:
  supports_cpu: true
  min_ram_gb: 2
```

### Schema Field Reference

#### Core Fields (Required)

| Field | Type | Description |
|-------|------|-------------|
| `schema_version` | string | Always `"1.1"` |
| `id` | string | Unique engine identifier |
| `runtime` | string | Runtime family (for model routing) |
| `stage` | string | Pipeline stage |
| `name` | string | Human-readable name |
| `version` | string | Semantic version |
| `execution_profile` | string | Runtime isolation profile: `inproc`, `venv`, or `container` |

#### execution_profile (Optional, defaults to `container`)

Controls where the runtime executes:

- `container`: existing distributed worker model via Redis streams and long-running engine containers
- `venv`: lite-mode subprocess execution in a runtime-specific virtualenv
- `inproc`: lite-mode direct execution inside the orchestrator process

`execution_profile` is execution policy only. It does not change task payloads, model identity (`runtime_model_id`), or output schemas. If the field is omitted, Dalston treats the runtime as `container` for backward compatibility.

#### container (Conditionally Required)

Required when `execution_profile: container`, or when `execution_profile` is omitted
(backward-compatible default is `container`).

For `venv` and `inproc` profiles, `container` may be omitted. In that case,
hardware metadata (`hardware.min_vram_gb`, `hardware.supports_cpu`, etc.) remains
the source of resource hints.

| Field | Type | Description |
|-------|------|-------------|
| `gpu` | string | `required`, `optional`, or `none` |
| `memory` | string | Recommended minimum (e.g., `8G`) |
| `model_cache` | string | Where to cache models |

#### capabilities (Required)

| Field | Type | Description |
|-------|------|-------------|
| `languages` | list/null | Language codes, or `null` for all |
| `max_audio_duration` | int | Max seconds |
| `streaming` | bool | Supports streaming? |
| `word_timestamps` | bool | Produces accurate word-level timestamps? |
| `includes_diarization` | bool | Output includes speaker labels? |

#### hf_compat (Optional)

| Field | Type | Description |
|-------|------|-------------|
| `pipeline_tag` | string | HF task taxonomy |
| `library_name` | string | Underlying ML framework |
| `license` | string | SPDX license identifier |

Valid `pipeline_tag` values:

- HF standard: `automatic-speech-recognition`, `speaker-diarization`, `voice-activity-detection`, `audio-classification`
- Dalston extensions: `dalston:audio-preparation`, `dalston:merge`, `dalston:pii-redaction`, `dalston:audio-redaction`

#### hardware (Optional)

| Field | Type | Description |
|-------|------|-------------|
| `min_vram_gb` | int | Minimum GPU VRAM in GB |
| `recommended_gpu` | list | GPU types: `a10g`, `t4`, `l4`, `a100`, `h100` |
| `supports_cpu` | bool | Whether CPU inference works |
| `min_ram_gb` | int | Minimum system RAM in GB |

#### performance (Optional)

| Field | Type | Description |
|-------|------|-------------|
| `rtf_gpu` | float | Real-time factor on GPU (0.05 = 20x faster) |
| `rtf_cpu` | float | Real-time factor on CPU, null if unsupported |
| `max_concurrent_jobs` | int | Concurrent job limit |
| `warm_start_latency_ms` | int | Latency after model loaded |

---

## Capabilities and Routing

Engine capabilities affect orchestrator routing and DAG construction.

### Routing Capabilities

| Field | Effect on Routing |
|-------|-------------------|
| `languages` | Filters engines by language support. `null` = all languages. |
| `streaming` | Required for real-time transcription jobs. |
| `word_timestamps` | If `true`, alignment stage is skipped. |
| `includes_diarization` | If `true`, diarize stage is skipped. |

### DAG Shape Examples

The orchestrator adapts DAG shape based on selected engine capabilities:

```
# faster-whisper (word_timestamps: false, includes_diarization: false)
prepare → transcribe → align → diarize → merge

# nemo/parakeet (word_timestamps: true, includes_diarization: false)
prepare → transcribe → diarize → merge  (no align - native timestamps)

# whisperx-full (word_timestamps: true, includes_diarization: true)
prepare → transcribe → merge  (no align, no diarize - all native)
```

### Ranking Criteria

When multiple engines match requirements, the selector prefers:

1. Native word timestamps (skips alignment stage)
2. Native diarization (skips diarize stage)
3. Language-specific over universal
4. Faster RTF (real-time factor)

---

## Engine SDK

All engines use the `dalston-engine-sdk` package for communication with the orchestrator.

### Base Engine Class

```python
from dalston.engine_sdk import (
    BatchTaskContext,
    Engine,
    EngineInput,
    EngineOutput,
    Segment,
    TranscribeOutput,
)
from dalston.engine_sdk.model_manager import ModelManager
from dalston.common.pipeline_types import TranscribeInput

class MyTranscribeEngine(Engine):
    """Runtime-based transcription engine."""

    def __init__(self):
        super().__init__()
        self.model_manager = MyModelManager(
            max_loaded=int(os.environ.get("DALSTON_MAX_LOADED_MODELS", "2")),
            ttl_seconds=int(os.environ.get("DALSTON_MODEL_TTL_SECONDS", "3600")),
        )

    def process(self, input: EngineInput, ctx: BatchTaskContext) -> EngineOutput:
        """Process a single task."""
        del ctx
        # Parse canonical typed transcribe params
        params: TranscribeInput = input.get_transcribe_params()
        model_id = params.runtime_model_id or os.environ.get("DALSTON_DEFAULT_MODEL_ID")

        # Acquire model (loads if needed)
        with self.model_manager.acquire(model_id) as model:
            result = model.transcribe(input.audio_path)

        return EngineOutput(
            data=TranscribeOutput(
                segments=[Segment(**s) for s in result.segments],
                text=result.text,
                language=result.language,
                runtime="my-runtime",
            )
        )


if __name__ == "__main__":
    engine = MyTranscribeEngine()
    engine.run()
```

### ModelManager

The `ModelManager` base class provides TTL-based, LRU-evicting model management:

```python
from dalston.engine_sdk.model_manager import ModelManager, LoadedModel

class MyModelManager(ModelManager[WhisperModel]):
    """Model manager for Whisper models."""

    def _load_model(self, model_id: str) -> WhisperModel:
        """Load a model from disk or S3."""
        # Check local cache first
        local_path = self.storage.get_local_path(model_id)
        if not local_path.exists():
            # Download from S3
            self.storage.download(model_id)

        return WhisperModel(str(local_path))

    def _unload_model(self, model: WhisperModel) -> None:
        """Unload a model and free GPU memory."""
        del model
        torch.cuda.synchronize()
        torch.cuda.empty_cache()
        gc.collect()
```

### S3ModelStorage

```python
from dalston.engine_sdk.model_storage import S3ModelStorage

storage = S3ModelStorage(
    bucket=os.environ["DALSTON_S3_BUCKET"],
    cache_dir=Path("/models/s3-cache"),
)

# Download model from S3 to local cache
local_path = storage.download("nvidia/parakeet-tdt-1.1b")

# Check if model is cached locally
if storage.is_cached("nvidia/parakeet-tdt-1.1b"):
    path = storage.get_local_path("nvidia/parakeet-tdt-1.1b")
```

### EngineInput

```python
@dataclass
class EngineInput:
    task_id: str
    job_id: str
    stage: str
    config: dict[str, Any]
    payload: dict[str, Any] | None
    previous_outputs: dict[str, Any]
    audio_path: Path | None             # Derived from materialized artifacts when present
    materialized_artifacts: dict[str, MaterializedArtifact]
```

M52 local runner command contract:

```bash
python -m dalston.engine_sdk.local_runner run \
  --engine engines.stt-transcribe.faster-whisper.engine:FasterWhisperEngine \
  --stage transcribe \
  --audio ./fixtures/audio.wav \
  --config ./fixtures/transcribe-config.json \
  --output ./tmp/output.json
```

`output.json` is always written with this envelope:

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

The `config` dict includes:

| Key | Description |
|-----|-------------|
| `runtime_model_id` | Runtime model identifier for the selected stage model |
| `language` | Language code or "auto" |
| `beam_size` | Beam search width |
| Other | Model-specific parameters |

### Engine Registration and Heartbeat

Engines register with Redis and publish heartbeats:

```python
# Heartbeat data includes:
{
    "engine_id": "faster-whisper",
    "instance_id": "fw-abc123",
    "stage": "transcribe",
    "status": "idle",              # idle, processing, offline
    "loaded_model": "Systran/faster-whisper-large-v3",
    "last_heartbeat": 1709395200,
    "capabilities": { ... }
}
```

The `loaded_model` field enables the console to show which model each engine instance has loaded.

---

## Creating a New Engine

### Quick Start with Scaffold Command

```bash
# Scaffold a new transcription runtime
python -m dalston.tools.scaffold_engine my-runtime --stage transcribe --gpu optional --no-dry-run

# Scaffold a utility engine
python -m dalston.tools.scaffold_engine my-merger --stage merge --gpu none --no-dry-run

# List all valid stages
python -m dalston.tools.scaffold_engine --list-stages
```

This creates:

```
engines/{stage}/{engine-id}/
├── engine.yaml          # Full schema 1.1 metadata
├── engine.py            # Engine implementation template
├── Dockerfile           # Container build file
├── requirements.txt     # Python dependencies
└── README.md            # Engine documentation
```

### Dockerfile Pattern

```dockerfile
FROM dalston/engine-base:latest

# Install dependencies
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy engine code
COPY engine.yaml /app/
COPY engine.py /app/

# Pre-download default model (optional)
ARG DEFAULT_MODEL=Systran/faster-whisper-base
RUN python -c "from faster_whisper import WhisperModel; WhisperModel('$DEFAULT_MODEL')"

CMD ["python", "/app/engine.py"]
```

### docker-compose.yml Service

```yaml
stt-batch-transcribe-faster-whisper:
  build:
    context: ./engines/stt-transcribe/faster-whisper
  environment:
    - REDIS_URL=redis://redis:6379
    - DALSTON_ENGINE_ID=faster-whisper
    - DALSTON_S3_BUCKET=${DALSTON_S3_BUCKET}
    - DALSTON_DEFAULT_MODEL_ID=Systran/faster-whisper-large-v3-turbo
    - DALSTON_MAX_LOADED_MODELS=2
    - DALSTON_MODEL_TTL_SECONDS=3600
  tmpfs:
    - /tmp/dalston:size=10G
  volumes:
    - model-cache:/models
  depends_on:
    - redis
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: 1
            capabilities: [gpu]
```

---

## Environment Variables

### Required

| Variable | Description |
|----------|-------------|
| `REDIS_URL` | Redis connection URL |
| `DALSTON_ENGINE_ID` | Engine/runtime identifier |
| `DALSTON_S3_BUCKET` | S3 bucket for models and artifacts |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `DALSTON_DEFAULT_MODEL_ID` | (none) | Default model if not specified in task |
| `DALSTON_MODEL_PRELOAD` | (none) | Model to load at startup |
| `DALSTON_MAX_LOADED_MODELS` | 2 | Max models in memory |
| `DALSTON_MODEL_TTL_SECONDS` | 3600 | Idle timeout before eviction |
| `DALSTON_LOG_LEVEL` | INFO | Logging level |
| `DALSTON_LOG_FORMAT` | json | Log format (json or text) |

---

## Built-in Engine Details

### faster-whisper

**Runtime**: `faster-whisper`
**Stage**: transcribe

CTranslate2-optimized Whisper for fast multilingual transcription.

**Supported Models**:

| Model | Size | Languages | CPU Support |
|-------|------|-----------|-------------|
| `Systran/faster-whisper-tiny` | 39M | 99 | Yes |
| `Systran/faster-whisper-base` | 74M | 99 | Yes |
| `Systran/faster-whisper-small` | 244M | 99 | Yes |
| `Systran/faster-whisper-medium` | 769M | 99 | Yes |
| `Systran/faster-whisper-large-v3` | 1.5G | 99 | Yes |
| `Systran/faster-whisper-large-v3-turbo` | 809M | 99 | Yes |

**Config Parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `runtime_model_id` | string | env default | HuggingFace model ID |
| `language` | string | `auto` | Language code or "auto" |
| `beam_size` | int | `5` | Beam search width |
| `vad_filter` | bool | `true` | Filter silence with VAD |

---

### nemo (Parakeet)

**Runtime**: `nemo`
**Stage**: transcribe

NVIDIA NeMo Parakeet models for high-accuracy English transcription.

**Supported Models**:

| Model | Size | Architecture | Word Timestamps |
|-------|------|--------------|-----------------|
| `nvidia/parakeet-ctc-0.6b` | 0.6B | CTC | Yes |
| `nvidia/parakeet-ctc-1.1b` | 1.1B | CTC | Yes |
| `nvidia/parakeet-tdt-0.6b-v3` | 0.6B | TDT | Yes |
| `nvidia/parakeet-tdt-1.1b` | 1.1B | TDT | Yes |

**Config Parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `runtime_model_id` | string | env default | HuggingFace model ID |

---

### vllm-asr

**Runtime**: `vllm-asr`
**Stage**: transcribe

Audio LLMs via vLLM for multilingual transcription with reasoning capabilities.

**Supported Models**:

| Model | Size | Languages |
|-------|------|-----------|
| `mistralai/Voxtral-Mini-3B-2507` | 3B | 13 |
| `mistralai/Voxtral-Small-24B-2507` | 24B | 13 |
| `Qwen/Qwen2-Audio-7B` | 7B | 8+ |

**Config Parameters**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `runtime_model_id` | string | env default | HuggingFace model ID |
| `max_tokens` | int | `4096` | Max output tokens |

---

### pyannote-4.0

**Stage**: diarize

State-of-the-art speaker diarization.

**Config**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `runtime_model_id` | string | required | Model registry runtime ID for diarization |
| `min_speakers` | int | `null` | Minimum speakers |
| `max_speakers` | int | `null` | Maximum speakers |
| `hf_token` | string | env | HuggingFace token |

---

### llm-cleanup

**Stage**: refine

LLM-based transcript refinement.

**Config**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `provider` | string | `anthropic` | LLM provider |
| `model` | string | `claude-sonnet-4-20250514` | Model name |
| `tasks` | array | all | Which tasks to run |

**Available Tasks**:

- `fix_transcription_errors` - Correct obvious mistakes
- `identify_speakers` - Name speakers from context
- `improve_punctuation` - Fix punctuation and capitalization
- `add_paragraphs` - Add paragraph breaks
- `generate_summary` - Create content summary

---

## Engine Health Monitoring

Engines report health via heartbeat:

```python
class Engine:
    def get_runtime_state(self) -> dict:
        return {
            "status": "idle",  # idle, processing
            "loaded_model": self.current_model_id,
            "memory_used_mb": get_memory_usage(),
            "gpu_memory_used_mb": get_gpu_memory_usage(),
        }
```

The orchestrator marks engines as unavailable if heartbeat expires (60s TTL).

---

## Validation and Catalog Generation

### Validate engine.yaml

```bash
# Validate single file
python -m dalston.tools.validate_engine engines/stt-transcribe/faster-whisper/engine.yaml

# Validate all engines
python -m dalston.tools.validate_engine --all
```

### Generate Catalog

The engine and model catalogs are generated from YAML files at build time:

```bash
# Generate from models/*.yaml and engines/*/engine.yaml
python scripts/generate_catalog.py \
  --models-dir models/ \
  --engines-dir engines/ \
  --output dalston/orchestrator/generated_catalog.json
```
