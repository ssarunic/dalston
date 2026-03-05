# Model Selection & Registry Specification

## Overview

Dalston uses a two-level architecture for model management:

1. **Model Catalog** (`/v1/models`): Static YAML definitions of known models, generated at build time
2. **Model Registry** (`/v1/models/registry`): PostgreSQL-backed tracking of download status, with S3 as canonical storage

This separation enables:

- Browse all available models (catalog) vs. see what's downloaded (registry)
- Dynamic model support via HuggingFace auto-routing
- Explicit model lifecycle management (pull, remove, sync)

---

## Architecture

### Two-Catalog Design

```
┌─────────────────┐     ┌─────────────────┐
│  Model Catalog  │     │ Model Registry  │
│ (models/*.yaml) │     │  (PostgreSQL)   │
├─────────────────┤     ├─────────────────┤
│ What CAN run    │     │ What IS ready   │
│ Static metadata │     │ Download status │
│ Build-time      │     │ Runtime state   │
└────────┬────────┘     └────────┬────────┘
         │                       │
         └───────────┬───────────┘
                     │
              ┌──────▼──────┐
              │   S3/MinIO  │
              │  (storage)  │
              └─────────────┘
```

### Model ID Format

Models use namespaced HuggingFace-style IDs:

| Format | Example | Description |
|--------|---------|-------------|
| `org/model` | `nvidia/parakeet-tdt-1.1b` | HuggingFace repo ID |
| `org/model` | `Systran/faster-whisper-large-v3` | HuggingFace repo ID |

This allows direct use of HuggingFace model IDs and unambiguous identification.

### Runtime Architecture

Models are served by **runtimes** - containerized engines that can load any compatible model:

| Runtime | Library | Description |
|---------|---------|-------------|
| `faster-whisper` | CTranslate2 | Whisper models in CTranslate2 format |
| `nemo` | NeMo | NVIDIA Parakeet/Canary models |
| `parakeet-onnx` | ONNX Runtime | NVIDIA Parakeet models in ONNX format |
| `hf-asr` | Transformers | Generic HuggingFace ASR pipeline |
| `vllm-asr` | vLLM | Audio LLMs (Voxtral, Qwen2-Audio) |

---

## Model Catalog

### YAML Schema (v1.1)

Models are defined in `models/*.yaml`:

```yaml
schema_version: "1.1"
id: nvidia/parakeet-tdt-1.1b              # Namespaced model ID
runtime: nemo                              # Engine runtime
runtime_model_id: "nvidia/parakeet-tdt-1.1b"  # HF ID for from_pretrained()

name: NVIDIA Parakeet TDT 1.1B
source: nvidia/parakeet-tdt-1.1b           # HuggingFace repo
size_gb: 4.2
stage: transcribe

description: |
  NVIDIA Parakeet FastConformer TDT 1.1B...

languages:
  - en                                     # null for multilingual

capabilities:
  word_timestamps: true
  punctuation: false
  capitalization: false
  streaming: false
  max_audio_duration: 7200

hardware:
  min_vram_gb: 6
  supports_cpu: true
  min_ram_gb: 12

performance:
  rtf_gpu: 0.0006                         # 0.0006 = 1666x faster than realtime
  rtf_cpu: null                           # null if unsupported
```

### Catalog API

```
GET /v1/models
GET /v1/models/{model_id}
```

Returns static catalog entries. Use for discovering available models.

**Response:**

```json
{
  "object": "list",
  "data": [
    {
      "id": "nvidia/parakeet-tdt-1.1b",
      "object": "model",
      "name": "NVIDIA Parakeet TDT 1.1B",
      "runtime": "nemo",
      "runtime_model_id": "nvidia/parakeet-tdt-1.1b",
      "source": "nvidia/parakeet-tdt-1.1b",
      "size_gb": 4.2,
      "stage": "transcribe",
      "languages": ["en"],
      "capabilities": {
        "word_timestamps": true,
        "punctuation": false,
        "capitalization": false,
        "streaming": false
      },
      "hardware": {
        "min_vram_gb": 6,
        "supports_cpu": true,
        "min_ram_gb": 12
      },
      "performance": {
        "rtf_gpu": 0.0006,
        "rtf_cpu": null
      }
    }
  ]
}
```

---

## Model Registry

### Database Schema

The `models` table tracks download status:

| Column | Type | Description |
|--------|------|-------------|
| `id` | String(200) | Namespaced model ID (PK) |
| `name` | String(200) | Human-readable name |
| `runtime` | String(50) | Engine runtime |
| `runtime_model_id` | String(200) | HF model ID for engine |
| `stage` | String(50) | Pipeline stage |
| `status` | String(20) | `not_downloaded`, `downloading`, `ready`, `failed` |
| `download_path` | Text | S3 URI |
| `size_bytes` | BigInteger | Downloaded size |
| `downloaded_at` | Timestamp | When downloaded |
| `source` | String(200) | HuggingFace repo ID |
| `library_name` | String(50) | ML library |
| `languages` | JSONB | Language codes |
| `word_timestamps` | Boolean | Capability |
| `punctuation` | Boolean | Capability |
| `capitalization` | Boolean | Capability |
| `streaming` | Boolean | Capability |
| `min_vram_gb` | Float | Hardware requirement |
| `min_ram_gb` | Float | Hardware requirement |
| `supports_cpu` | Boolean | Hardware capability |
| `model_metadata` | JSONB | HF card data |
| `last_used_at` | Timestamp | Usage tracking |

### Status Flow

```
not_downloaded → downloading → ready
                     ↓
                  failed
```

### Registry API

```
GET    /v1/models/registry                 # List with download status
GET    /v1/models/registry/{model_id}      # Get single entry
POST   /v1/models/{model_id}/pull          # Download from HuggingFace
DELETE /v1/models/{model_id}               # Remove files (purge=true deletes entry)
POST   /v1/models/sync                     # Sync registry with S3
```

**List Registry Response:**

```json
{
  "object": "list",
  "data": [
    {
      "id": "nvidia/parakeet-tdt-1.1b",
      "object": "model",
      "name": "NVIDIA Parakeet TDT 1.1B",
      "runtime": "nemo",
      "runtime_model_id": "nvidia/parakeet-tdt-1.1b",
      "stage": "transcribe",
      "status": "ready",
      "download_path": "s3://dalston-artifacts/models/nvidia/parakeet-tdt-1.1b/",
      "size_bytes": 4509715968,
      "downloaded_at": "2026-03-02T15:30:00Z",
      "source": "nvidia/parakeet-tdt-1.1b",
      "languages": ["en"],
      "word_timestamps": true,
      "punctuation": false,
      "capitalization": false,
      "supports_cpu": true,
      "metadata": {
        "downloads": 125000,
        "likes": 450,
        "pipeline_tag": "automatic-speech-recognition"
      }
    }
  ]
}
```

---

## HuggingFace Auto-Routing

Dalston can automatically route arbitrary HuggingFace models to the appropriate runtime.

### Routing Priority

1. **`library_name`** (most reliable):
   - `ctranslate2` → `faster-whisper`
   - `nemo` → `nemo`
   - `transformers` + ASR pipeline → `hf-asr`
   - `vllm` → `vllm-asr`

2. **Tags** (fallback):
   - `faster-whisper`, `ctranslate2` → `faster-whisper`
   - `nemo` → `nemo`
   - `whisper` → `faster-whisper`

3. **`pipeline_tag`** (last resort):
   - `automatic-speech-recognition` → `hf-asr`

### Resolution API

```
POST /v1/models/hf/resolve
GET  /v1/models/hf/mappings
```

**Resolve Request:**

```json
{
  "model_id": "openai/whisper-large-v3",
  "auto_register": true
}
```

**Resolve Response:**

```json
{
  "model_id": "openai/whisper-large-v3",
  "library_name": "ctranslate2",
  "pipeline_tag": "automatic-speech-recognition",
  "tags": ["whisper", "ctranslate2"],
  "languages": [],
  "downloads": 1500000,
  "likes": 3200,
  "resolved_runtime": "faster-whisper",
  "can_route": true
}
```

---

## Model Selection in Jobs

### Specifying a Model

```bash
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $API_KEY" \
  -F "file=@audio.wav" \
  -F "model=nvidia/parakeet-tdt-1.1b"
```

### Stage-Specific Model Selection (M55)

Non-transcribe stages use dedicated model parameters:

- `model_diarize`
- `model_align`
- `model_pii_detect`

Example:

```bash
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $API_KEY" \
  -F "file=@audio.wav" \
  -F "model=auto" \
  -F "speaker_detection=diarize" \
  -F "pii_detection=true" \
  -F "model_diarize=pyannote/speaker-diarization-community-1" \
  -F "model_align=facebook/wav2vec2-base-960h" \
  -F "model_pii_detect=urchade/gliner_multi-v2.1"
```

Selection rules:

1. Stage model IDs must exist in the model registry.
2. `model.stage` must match the requested stage (`model_stage_mismatch` on mismatch).
3. Model status must be `ready` (`model_not_ready` otherwise).
4. Selected runtime must be available (`runtime_unavailable` otherwise).

### Auto-Selection

Omit the model parameter or use `auto` for capability-driven selection:

```bash
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $API_KEY" \
  -F "file=@audio.wav" \
  -F "model=auto"
```

The orchestrator selects the best downloaded model based on:

1. Language compatibility
2. Required capabilities (word timestamps, streaming)
3. Engine availability
4. Performance characteristics (RTF)

### Selection Algorithm

When `model=auto`:

1. Query registry for `status=ready` models matching requirements
2. Filter by language support (if specified)
3. Rank by: word timestamps → diarization → language specificity → RTF
4. Select best match and route to appropriate runtime

---

## Engine Types

### Batch Transcription Engines

| Runtime | Models | Languages | Word Timestamps | GPU Required |
|---------|--------|-----------|-----------------|--------------|
| `faster-whisper` | Whisper variants | 99 | Via alignment | No (CPU supported) |
| `nemo` | Parakeet CTC/TDT/RNNT | 1 (en) | Native | Yes (CPU supported) |
| `parakeet-onnx` | Parakeet ONNX | 1 (en) | Native | No (ONNX Runtime) |
| `hf-asr` | Any HF ASR model | Varies | Depends on model | Varies |
| `vllm-asr` | Voxtral, Qwen2-Audio | 13+ | Yes | Yes |

### Real-time Streaming Engines

| Runtime | Models | Languages | Use Case |
|---------|--------|-----------|----------|
| `faster-whisper-rt` | Whisper streaming | 99 | Low-latency multilingual |
| `parakeet-rt` | Parakeet streaming | 1 (en) | Fast English streaming |
| `parakeet-onnx-rt` | Parakeet ONNX streaming | 1 (en) | Edge deployment |
| `voxtral-rt` | Voxtral Mini Realtime | 13 | Multilingual streaming |

---

## Web Console

The Models page (`/models`) provides:

### Model Registry Table

- **Columns**: Model ID, Runtime, Status, Size, Capabilities, Actions
- **Status indicators**:
  - Green: `ready` (downloaded)
  - Yellow pulse: `downloading`
  - Gray: `not_downloaded` (available)
  - Red: `failed`
- **Expandable rows**: Show hardware requirements, languages, HF metadata

### Actions

| Action | Status | Behavior |
|--------|--------|----------|
| Download | `not_downloaded`, `failed` | Start HuggingFace download |
| Remove | `ready` | Delete S3 files, keep registry entry |
| Delete | Any (not downloading) | Remove files and registry entry |

### Filters

- Search: Text search across ID, name, runtime
- Stage: transcribe, diarize, align, etc.
- Runtime: faster-whisper, nemo, etc.
- Status: ready, not_downloaded, downloading, failed

### Add from HuggingFace

1. Click "Add from HF" button
2. Search HuggingFace Hub (live autocomplete)
3. Select model → auto-resolve runtime
4. Model added to registry with `not_downloaded` status

---

## Error Handling

### Model Not Found

```json
{
  "error": {
    "type": "invalid_request_error",
    "message": "Model 'unknown/model' not found in catalog or registry",
    "param": "model"
  }
}
```

### Model Not Downloaded

```json
{
  "error": {
    "type": "invalid_request_error",
    "message": "Model 'nvidia/parakeet-tdt-1.1b' not downloaded. Run: dalston model pull nvidia/parakeet-tdt-1.1b"
  }
}
```

### No Compatible Engine Running

```json
{
  "error": {
    "type": "service_unavailable",
    "message": "No engine available for runtime 'nemo'. Start an engine with: docker compose up -d stt-batch-transcribe-nemo"
  }
}
```

### Model In Use

```json
{
  "error": {
    "type": "conflict",
    "message": "Cannot delete model nvidia/parakeet-tdt-1.1b: 3 pending/processing job(s) using it"
  }
}
```

---

## S3 Storage Structure

Models are stored in S3 with a completion marker:

```
s3://dalston-artifacts/
└── models/
    └── nvidia/parakeet-tdt-1.1b/
        ├── model.nemo
        ├── config.yaml
        └── .complete           # Atomic completion marker
```

The `.complete` marker ensures atomic uploads - a model is only considered ready when this file exists.

---

## Runtime Model Management

Engines load models on demand:

1. **Job arrives** with `runtime_model_id` in config
2. **Engine checks** if model is loaded in memory
3. **If different model**: Unload current, free GPU memory, load new
4. **If model not on disk**: Download from S3 to local cache
5. **Process audio** and return results

### Model Manager

Each engine has a `ModelManager` that handles:

- **TTL-based eviction**: Unload idle models after `DALSTON_MODEL_TTL_SECONDS` (default: 3600)
- **LRU eviction**: When at `DALSTON_MAX_LOADED_MODELS`, evict least-recently-used
- **Reference counting**: Models in use are never evicted
- **Preloading**: `DALSTON_MODEL_PRELOAD` loads a model at startup

### S3-to-Local Caching

```
S3: s3://bucket/models/{model_id}/
      ↓ download on first use
Local: /models/s3-cache/{safe_model_id}/
```

The `s3-cache` directory persists across container restarts via Docker volume.

---

## Audit Logging

Model operations are audit-logged:

| Event | Data |
|-------|------|
| `model.downloaded` | model_id, source, size_bytes, download_path |
| `model.removed` | model_id, download_path |
| `model.download_failed` | model_id, error |
| `model.deleted_from_registry` | model_id, download_path |

---

## CLI Commands

```bash
# List models (catalog)
dalston model ls

# List registry with download status
dalston model ls --registry

# Download a model
dalston model pull nvidia/parakeet-tdt-1.1b

# Check model status
dalston model status nvidia/parakeet-tdt-1.1b

# Remove downloaded files
dalston model rm nvidia/parakeet-tdt-1.1b

# Delete from registry entirely
dalston model rm --purge nvidia/parakeet-tdt-1.1b

# Sync registry with S3
dalston model sync
```

---

## Migration from Legacy

The system migrated from short IDs to namespaced IDs:

| Old Format | New Format |
|------------|------------|
| `parakeet-tdt-1.1b` | `nvidia/parakeet-tdt-1.1b` |
| `faster-whisper-large-v3` | `Systran/faster-whisper-large-v3` |

Migration `0026` handled the conversion automatically.
