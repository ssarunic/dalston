# M14: Model Selection

| | |
|---|---|
| **Goal** | Allow users to select transcription models via API parameter |
| **Duration** | 2-3 days |
| **Dependencies** | M2 (transcription working) |
| **Deliverable** | `model` parameter in REST/WebSocket APIs, `/v1/models` endpoint |
| **Status** | Not Started |

## User Story

> *"As a developer, I want to choose between different transcription models (whisper-large-v3, whisper-base, distil-whisper) to balance accuracy vs speed for my use case, without knowing the internal engine architecture."*

---

## Overview

Currently, model selection is not exposed in the API - all transcriptions use `large-v3` by default. This milestone adds:

1. **Model registry** - Central mapping of model IDs to engine configuration
2. **API parameter** - `model` parameter on batch and real-time endpoints
3. **Models endpoint** - `GET /v1/models` to list available models
4. **Validation** - Clear errors for invalid model names

---

## Design

Uses **provider-style model names** (like OpenAI's `gpt-4o`, Anthropic's `claude-sonnet-4-20250514`):

```
whisper-large-v3      # Default, most accurate
whisper-base          # Faster, less accurate
distil-whisper        # English-only, very fast
parakeet-1.1b         # Future: NVIDIA streaming model
```

Model name implies which engine to use - no separate `engine_transcribe` parameter needed for most users.

See [MODEL_SELECTION.md](/docs/specs/MODEL_SELECTION.md) for full specification.

---

## Steps

### 14.1: Model Registry & Batch API

**Deliverables:**

- Create `dalston/common/models.py` with model registry
- Add `model` parameter to `POST /v1/audio/transcriptions`
- Update orchestrator to use model's engine and config
- Validation and error handling

### 14.2: Models Endpoint

**Deliverables:**

- `GET /v1/models` - List all available models
- `GET /v1/models/{model_id}` - Get model details
- Include capabilities metadata (languages, streaming, word_timestamps)

### 14.3: Real-time API Updates

**Deliverables:**

- Accept full model IDs in WebSocket `model` query param
- Existing `fast`/`accurate` values work as aliases
- Update session router to use registry

### 14.4: ElevenLabs Compatibility

**Deliverables:**

- Accept `model_id` parameter (ElevenLabs naming) as alias for `model`
- Document mapping in compatibility docs

---

## Verification

### Test Model Selection

```bash
# Default model
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@test.wav"
# → Uses whisper-large-v3

# Specific model
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@test.wav" \
  -F "model=whisper-base"
# → Uses faster-whisper with base model

# Alias
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@test.wav" \
  -F "model=fast"
# → Uses distil-whisper

# Invalid model
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@test.wav" \
  -F "model=nonexistent"
# → Returns 400 with available models list
```

### Test Models Endpoint

```bash
# List models
curl http://localhost:8000/v1/models
# → Returns list with id, name, description, capabilities

# Get specific model
curl http://localhost:8000/v1/models/whisper-large-v3
# → Returns full model details including engine info
```

### Test Real-time

```bash
# WebSocket with model
websocat "ws://localhost:8000/v1/audio/transcriptions/stream?model=whisper-base"
# → Session uses base model
```

---

## Checkpoint

- [ ] **Model registry** created with whisper variants
- [ ] **Batch API** accepts `model` parameter
- [ ] **Orchestrator** routes to correct engine based on model
- [ ] **Models endpoint** returns available models
- [ ] **Real-time** accepts full model IDs
- [ ] **Aliases** work (fast, accurate, large, base)
- [ ] **Validation** returns helpful error for invalid models
- [ ] **Tests** cover model selection flow

---

## Future Enhancements

1. **Dynamic registry** - Load models from engine.yaml files at startup
2. **Model routing by capability** - `model=auto` with `language=hr` picks best Croatian model
3. **Version pinning** - `whisper-large-v3@20240101` for reproducibility
4. **Per-engine model variants** - When Parakeet is added, registry expands automatically
