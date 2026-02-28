# Dalston Engines Reference

## Overview

Engines are containerized processors that implement one or more pipeline stages. Each engine runs in its own Docker container with isolated dependencies.

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

Speech-to-text conversion.

| Engine ID | Description | GPU | Languages |
|-----------|-------------|-----|-----------|
| `faster-whisper` | Fast Whisper (CTranslate2), multilingual | Yes | All |
| `parakeet` | NVIDIA Parakeet, English-optimized, very fast | Yes | English |
| `whisper-openai` | Original OpenAI Whisper | Yes | All |
| `distil-whisper` | Distilled Whisper, faster but slightly less accurate | Yes | English |

### ALIGN

Word-level timestamp alignment.

| Engine ID | Description | GPU |
|-----------|-------------|-----|
| `phoneme-align` | Standalone CTC forced alignment (wav2vec2-based) | Optional |

### DIARIZE

Speaker identification and segmentation.

| Engine ID | Description | GPU |
|-----------|-------------|-----|
| `pyannote-3.1` | Pyannote 3.1, stable release | Yes |
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

### MULTI-STAGE

Integrated engines covering multiple stages.

| Engine ID | Stages | Description | GPU |
|-----------|--------|-------------|-----|
| `whisperx-full` | transcribe, align, diarize | Full WhisperX pipeline | Yes |

---

## Engine Metadata Format

Each engine has an `engine.yaml` file (schema version 1.1) describing its capabilities. This file is the single source of truth for engine metadata.

```yaml
# === REQUIRED FIELDS ===
schema_version: "1.1"                # Schema version for validation
id: faster-whisper                   # Unique engine identifier
stage: transcribe                    # Pipeline stage (or type: realtime)
name: Faster Whisper
version: 1.2.0
description: |
  CTranslate2-optimized Whisper implementation.
  Supports all Whisper model sizes, multiple languages.

container:
  gpu: required                      # required | optional | none
  memory: 8G                         # Recommended minimum
  model_cache: /models               # Where to cache models

capabilities:
  languages:
    - all                            # Or explicit list: [en, es, fr, ...]
  max_audio_duration: 7200           # Seconds
  streaming: false                   # Supports streaming?
  word_timestamps: true              # Produces accurate word-level timestamps?
  includes_diarization: false        # Output includes speaker labels?

# === OPTIONAL FIELDS ===
input:
  audio_formats: [wav]               # Expected input format
  sample_rate: 16000                 # Expected sample rate
  channels: 1                        # Expected channels (mono)

config_schema:
  type: object
  properties:
    model:
      type: string
      enum: [tiny, base, small, medium, large-v2, large-v3]
      default: large-v3
    language:
      type: string
      default: auto

output_schema:
  type: object
  required: [text, segments, language]
  properties:
    text:
      type: string
    segments:
      type: array
    language:
      type: string

# === NEW IN SCHEMA 1.1 ===

# HuggingFace ecosystem compatibility
hf_compat:
  pipeline_tag: automatic-speech-recognition  # HF task taxonomy
  library_name: ctranslate2                   # Underlying ML framework
  license: mit                                # SPDX license identifier

# Hardware requirements
hardware:
  min_vram_gb: 4                     # Minimum GPU VRAM in GB
  recommended_gpu:                   # Recommended GPU types
    - t4
    - a10g
  supports_cpu: true                 # Whether CPU inference works
  min_ram_gb: 8                      # Minimum system RAM in GB

# Performance characteristics
performance:
  rtf_gpu: 0.05                      # Real-time factor on GPU (0.05 = 20x faster)
  rtf_cpu: 0.8                       # Real-time factor on CPU
  max_concurrent_jobs: 4             # Concurrent job limit
  warm_start_latency_ms: 50          # Latency after model loaded
```

### Schema 1.1 New Sections

#### hf_compat (optional)

HuggingFace ecosystem compatibility metadata.

| Field | Type | Description |
|-------|------|-------------|
| `pipeline_tag` | string | HF task taxonomy or Dalston extension |
| `library_name` | string | Underlying ML framework |
| `license` | string | SPDX license identifier |

**Valid pipeline_tag values:**

- HF standard: `automatic-speech-recognition`, `speaker-diarization`, `voice-activity-detection`, `audio-classification`
- Dalston extensions: `dalston:audio-preparation`, `dalston:merge`, `dalston:pii-redaction`, `dalston:audio-redaction`

#### hardware (optional)

Hardware requirements for deployment planning and auto-scaling.

| Field | Type | Description |
|-------|------|-------------|
| `min_vram_gb` | int | Minimum GPU VRAM in GB |
| `recommended_gpu` | list | GPU types: `a10g`, `t4`, `l4`, `a100`, `h100` |
| `supports_cpu` | bool | Whether CPU inference works |
| `min_ram_gb` | int | Minimum system RAM in GB |

#### performance (optional)

Performance characteristics for timeout calculation and capacity planning.

| Field | Type | Description |
|-------|------|-------------|
| `rtf_gpu` | float | Real-time factor on GPU (0.05 = 20x faster than real-time) |
| `rtf_cpu` | float | Real-time factor on CPU, null if unsupported |
| `max_concurrent_jobs` | int | Concurrent job limit |
| `warm_start_latency_ms` | int | Latency after model loaded |

### Capabilities and Routing

Engine capabilities directly affect how the orchestrator selects engines and builds task DAGs.

#### Routing Capabilities

| Field | Type | Effect on Routing |
| --- | --- | --- |
| `languages` | list or null | Filters engines by language support. `null` = universal (all languages). |
| `streaming` | bool | Required for real-time transcription jobs. |
| `word_timestamps` | bool | If `true`, alignment stage is skipped (engine produces accurate word timing). |
| `includes_diarization` | bool | If `true`, diarize stage is skipped (output includes speaker labels). |

#### DAG Shape Examples

The orchestrator adapts DAG shape based on selected engine capabilities:

```
# faster-whisper (word_timestamps: false, includes_diarization: false)
prepare → transcribe → align → diarize → merge

# parakeet (word_timestamps: true, includes_diarization: false)
prepare → transcribe → diarize → merge  (no align - native timestamps)

# whisperx-full (word_timestamps: true, includes_diarization: true)
prepare → transcribe → merge  (no align, no diarize - all native)
```

#### Ranking Criteria

When multiple engines match requirements, the selector prefers:

1. Native word timestamps (skips alignment stage)
2. Native diarization (skips diarize stage)
3. Language-specific over universal
4. Faster RTF (real-time factor)

### Validation

All engine.yaml files are validated against the JSON Schema at `dalston/schemas/engine.schema.json`:

```bash
# Validate single file
python -m dalston.tools.validate_engine engines/transcribe/faster-whisper/engine.yaml

# Validate all engines
python -m dalston.tools.validate_engine --all
```

### Catalog Generation

The engine catalog is generated from engine.yaml files at build time:

```bash
python scripts/generate_catalog.py --engines-dir engines/ --output dalston/orchestrator/generated_catalog.json
```

---

## Engine SDK

All engines use the `dalston-engine-sdk` package for communication with the orchestrator.

### Base Engine Class

```python
from dalston_engine_sdk import Engine, TaskInput, TaskOutput

class MyEngine(Engine):
    """Custom engine implementation."""

    def __init__(self):
        super().__init__()
        self.model = None

    def load_model(self, config: dict):
        """Load model (called once, cached)."""
        if self.model is None:
            self.model = load_my_model(config)

    def process(self, input: TaskInput) -> TaskOutput:
        """Process a single task."""
        self.load_model(input.config)

        result = self.model.process(input.audio_path)

        return TaskOutput(data=result)


if __name__ == "__main__":
    engine = MyEngine()
    engine.run()  # SDK handles stream polling
```

### TaskInput

```python
@dataclass
class TaskInput:
    task_id: str
    job_id: str
    audio_path: Path                    # Primary audio file
    previous_outputs: dict[str, Any]    # Results from dependency tasks
    config: dict[str, Any]              # Engine-specific config
```

### TaskOutput

```python
@dataclass
class TaskOutput:
    data: dict[str, Any]                # Structured result
    artifacts: dict[str, Path] = None   # Additional files produced
```

### SDK Runner Loop

The SDK handles:

1. Connecting to Redis (for stream polling)
2. Polling the engine's stream via consumer group (`dalston:stream:{engine_id}`)
3. Downloading task input from S3 to local temp
4. Calling `engine.process()`
5. Uploading task output to S3
6. Acknowledging the stream message (`XACK`) and publishing completion event
7. Cleaning up local temp files
8. Error handling and reporting

```python
class Engine:
    def run(self):
        """Main loop - SDK implementation."""
        redis = Redis.from_url(os.environ["REDIS_URL"])
        engine_id = os.environ["ENGINE_ID"]
        stream_key = f"dalston:stream:{engine_id}"

        while True:
            # Blocking read from stream via consumer group
            results = redis.xreadgroup(
                groupname="engines",
                consumername=engine_id,
                streams={stream_key: ">"},
                count=1,
                block=30000,
            )

            if not results:
                continue  # Timeout, check again

            _stream, entries = results[0]
            message_id, fields = entries[0]
            task_id = fields["task_id"]

            try:
                # Load task
                task = self.load_task(task_id)
                self.update_status(task, "running")

                # Load input
                input = self.load_input(task)

                # Process
                output = self.process(input)

                # Save output
                self.save_output(task, output)
                self.update_status(task, "completed")

                # Acknowledge stream message
                redis.xack(stream_key, "engines", message_id)

                # Publish event
                redis.publish("dalston:events", json.dumps({
                    "type": "task.completed",
                    "task_id": task_id,
                    "job_id": task.job_id
                }))

            except Exception as e:
                self.update_status(task, "failed", error=str(e))
                redis.publish("dalston:events", json.dumps({
                    "type": "task.failed",
                    "task_id": task_id,
                    "job_id": task.job_id,
                    "error": str(e)
                }))
```

---

## Creating a New Engine

### Quick Start with Scaffold Command

The easiest way to create a new engine is using the scaffold command:

```bash
# Scaffold a new transcription engine
python -m dalston.tools.scaffold_engine my-transcriber --stage transcribe --no-dry-run

# Scaffold a diarization engine with GPU required
python -m dalston.tools.scaffold_engine my-diarizer --stage diarize --gpu required --no-dry-run

# Scaffold a CPU-only merge engine
python -m dalston.tools.scaffold_engine my-merger --stage merge --gpu none --no-dry-run

# List all valid stages
python -m dalston.tools.scaffold_engine --list-stages
```

This creates a complete engine skeleton:

```
engines/{stage}/{engine-id}/
├── engine.yaml          # Full schema 1.1 metadata
├── engine.py            # Engine implementation template
├── Dockerfile           # Container build file
├── requirements.txt     # Python dependencies
└── README.md            # Engine documentation
```

### Manual Setup

Alternatively, create the structure manually:

### 1. Create Directory Structure

```
engines/
└── {stage}/
    └── {engine-id}/
        ├── Dockerfile
        ├── requirements.txt
        ├── engine.yaml
        └── engine.py
```

### 2. Write engine.yaml

Define metadata, capabilities, and configuration schema (see Engine Metadata Format above).

### 3. Implement engine.py

```python
from dalston_engine_sdk import Engine, TaskInput, TaskOutput

class MyNewEngine(Engine):
    def process(self, input: TaskInput) -> TaskOutput:
        # Your implementation here
        result = do_processing(input.audio_path, **input.config)
        return TaskOutput(data=result)

if __name__ == "__main__":
    MyNewEngine().run()
```

### 4. Create Dockerfile

```dockerfile
FROM dalston/engine-base:latest

# Install dependencies
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy engine code
COPY engine.yaml /app/
COPY engine.py /app/

# Pre-download models (optional, for faster startup)
RUN python -c "import my_model; my_model.download()"

CMD ["python", "/app/engine.py"]
```

### 5. Add to docker-compose.yml

```yaml
engine-my-new-engine:
  build:
    context: ./engines/{stage}/{engine-id}
  environment:
    - REDIS_URL=redis://redis:6379
    - ENGINE_ID={engine-id}
    - DALSTON_S3_BUCKET=${DALSTON_S3_BUCKET}
    - DALSTON_S3_REGION=${DALSTON_S3_REGION}
    - AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
    - AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}
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
            capabilities: [gpu]  # If GPU required
```

---

## Built-in Engine Details

### faster-whisper

**Stage**: transcribe

Fast Whisper implementation using CTranslate2 for optimized inference.

**Config**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `model` | string | `large-v3` | Model size |
| `language` | string | `auto` | Language code or "auto" |
| `beam_size` | int | `5` | Beam search width |
| `vad_filter` | bool | `true` | Filter silence with VAD |

**Output**:

```json
{
  "text": "Full transcript...",
  "segments": [
    {
      "start": 0.0,
      "end": 3.5,
      "text": "Segment text",
      "words": [{"text": "...", "start": 0.0, "end": 0.4, "confidence": 0.98}]
    }
  ],
  "language": "en",
  "language_confidence": 0.98
}
```

---

### pyannote-3.1

**Stage**: diarize

State-of-the-art speaker diarization.

**Config**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `min_speakers` | int | `null` | Minimum speakers |
| `max_speakers` | int | `null` | Maximum speakers |
| `hf_token` | string | env | HuggingFace token |

**Output**:

```json
{
  "speakers": ["SPEAKER_00", "SPEAKER_01"],
  "segments": [
    {"start": 0.0, "end": 3.5, "speaker": "SPEAKER_00"},
    {"start": 3.5, "end": 7.2, "speaker": "SPEAKER_01"}
  ]
}
```

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

- `fix_transcription_errors` — Correct obvious mistakes
- `identify_speakers` — Name speakers from context
- `improve_punctuation` — Fix punctuation and capitalization
- `add_paragraphs` — Add paragraph breaks
- `generate_summary` — Create content summary

**Output**:

```json
{
  "segments": [...],          // Corrected segments
  "speakers": [
    {"id": "SPEAKER_00", "label": "John Smith"}
  ],
  "paragraphs": [...],
  "summary": "..."
}
```

---

### whisperx-full

**Stages**: transcribe, align, diarize

Integrated WhisperX pipeline in a single engine.

**Config**:

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `model` | string | `large-v3` | Whisper model |
| `language` | string | `auto` | Language code |
| `min_speakers` | int | `null` | Min speakers for diarization |
| `max_speakers` | int | `null` | Max speakers |
| `hf_token` | string | env | HuggingFace token |

**Output**:
Combined output with transcription, alignment, and diarization already merged:

```json
{
  "text": "...",
  "language": "en",
  "segments": [
    {
      "start": 0.0,
      "end": 3.5,
      "text": "...",
      "speaker": "SPEAKER_00",
      "words": [...]
    }
  ],
  "speakers": ["SPEAKER_00", "SPEAKER_01"]
}
```

---

## Engine Health Monitoring

Engines should respond to health checks:

```python
class Engine:
    def health_check(self) -> dict:
        return {
            "status": "healthy",
            "model_loaded": self.model is not None,
            "gpu_available": torch.cuda.is_available(),
            "memory_used": get_memory_usage()
        }
```

The orchestrator periodically checks engine health and marks unhealthy engines as unavailable.
