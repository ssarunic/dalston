# Model Selection Specification

## Overview

This specification defines how users select transcription engines in Dalston's API. The system uses a direct engine ID approach - users either specify an exact engine ID or use `auto` for capability-driven selection.

---

## Design Principles

1. **Direct mapping**: Model parameter maps directly to engine IDs
2. **Auto-selection**: Use `auto` to let the orchestrator select the best available engine
3. **Runtime discovery**: `/v1/models` shows only currently running engines
4. **No aliases**: Clean break - use exact engine IDs for explicit selection

---

## Engine Selection

### Specifying an Engine

Users can specify an exact engine ID:

```bash
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $API_KEY" \
  -F "file=@audio.wav" \
  -F "model=faster-whisper-base"
```

### Auto-Selection

Use `auto` (or omit the parameter) for capability-driven selection:

```bash
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $API_KEY" \
  -F "file=@audio.wav" \
  -F "model=auto"
```

The orchestrator selects the best engine based on:

- Required capabilities (language, streaming, word timestamps)
- Engine availability (running and healthy)
- Priority/performance characteristics from catalog

---

## Available Engines

### Batch Transcription Engines

| Engine ID | Languages | Word Timestamps | GPU Required |
|-----------|-----------|-----------------|--------------|
| `faster-whisper-base` | 99 | Via alignment | No (CPU supported) |
| `faster-whisper-large-v3` | 99 | Via alignment | Yes |
| `faster-whisper-large-v3-turbo` | 99 | Via alignment | Yes |
| `parakeet-0.6b` | 1 (en) | Native | No (CPU supported) |
| `parakeet-1.1b` | 1 (en) | Native | Yes |

### Realtime Streaming Engines

| Engine ID | Languages | Use Case |
|-----------|-----------|----------|
| `whisper-streaming-base` | 99 | Low-latency multilingual |
| `parakeet-streaming-0.6b` | 1 (en) | Fast English streaming |
| `parakeet-streaming-1.1b` | 1 (en) | Accurate English streaming |

---

## API Endpoints

### Batch Transcription

```
POST /v1/audio/transcriptions
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `model` | string | `auto` | Engine ID or `auto` for auto-selection |

### Real-time Transcription

```
GET /v1/audio/transcriptions/stream?model=parakeet-streaming-0.6b
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `model` | string | `auto` | Engine ID or `auto` for auto-selection |

### List Running Engines

```
GET /v1/models
```

Returns only engines that are currently running and healthy:

```json
{
  "object": "list",
  "data": [
    {
      "id": "faster-whisper-base",
      "object": "model",
      "stage": "transcribe",
      "status": "running",
      "capabilities": {
        "languages": ["en", "es", "fr", ...],
        "streaming": false,
        "word_timestamps": false
      }
    },
    {
      "id": "parakeet-0.6b",
      "object": "model",
      "stage": "transcribe",
      "status": "running",
      "capabilities": {
        "languages": ["en"],
        "streaming": false,
        "word_timestamps": true
      }
    }
  ]
}
```

### List All Engines (with status)

```
GET /v1/engines
```

Returns all engines from catalog with their current status:

```json
{
  "engines": [
    {
      "id": "faster-whisper-base",
      "stage": "transcribe",
      "version": "1.0.0",
      "status": "running",
      "capabilities": { ... },
      "hardware": { ... },
      "performance": { ... }
    },
    {
      "id": "faster-whisper-large-v3",
      "stage": "transcribe",
      "version": "1.0.0",
      "status": "available",
      "capabilities": { ... },
      "hardware": { ... },
      "performance": { ... }
    }
  ],
  "total": 2
}
```

Status values:

- `running`: Engine has valid heartbeat
- `available`: In catalog but not running
- `unhealthy`: Heartbeat expired

---

## Error Handling

### Engine Not Found

When a specified engine doesn't exist in the catalog:

```json
{
  "error": {
    "type": "invalid_request_error",
    "message": "Engine 'unknown-engine' not found",
    "param": "model"
  }
}
```

### Engine Not Running

When a specified engine exists but isn't currently running:

```json
{
  "error": {
    "type": "service_unavailable",
    "message": "Engine 'faster-whisper-large-v3' is not currently running"
  }
}
```

### Capability Mismatch

When auto-selection can't find an engine matching requirements:

```json
{
  "error": {
    "type": "service_unavailable",
    "message": "No engine available for stage 'transcribe' with language 'hr'"
  }
}
```

---

## ElevenLabs Compatibility

The `/v1/speech-to-text` endpoint accepts ElevenLabs `model_id` parameter values:

| ElevenLabs model_id | Behavior |
|---------------------|----------|
| `scribe_v1` | Auto-select (treated as `auto`) |
| `scribe_v2` | Auto-select (treated as `auto`) |
| Any other value | Auto-select (treated as `auto`) |

ElevenLabs model names are treated as auto-selection since they don't map to Dalston engines.

---

## Adding New Engines

When adding a new transcription engine:

1. Implement engine in `engines/{stage}/{engine-id}/`
2. Create variant YAML files in `engines/{stage}/{engine-id}/variants/`
3. Add docker-compose service definition
4. Engine auto-registers via heartbeat when started
5. Appears in `/v1/models` when running

See [M32: Engine Variant Structure](../plan/milestones/M32-engine-variant-structure.md) for the variant pattern.

---

## Future Enhancements

### Capability-Based Auto-Selection

Future: richer auto-selection based on requirements:

```json
{
  "model": "auto",
  "language": "hr",
  "streaming": true
}
```

Would route to the best engine supporting Croatian with streaming.

### Quality Tiers

Future: tier-based selection as convenience:

```json
{
  "model": "auto",
  "tier": "fast"
}
```

Would route to the fastest available engine.

---

## Models of Interest

Future models being evaluated for integration into Dalston.

### Voxtral Mini 4B Realtime (Mistral AI)

**Released:** February 2026 as open weights (Apache 2.0)
**HuggingFace:** [mistralai/Voxtral-Mini-4B-Realtime-2602](https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602)

Voxtral Mini 4B Realtime 2602 is a multilingual, natively-streaming speech transcription model - among the first open-source solutions to achieve accuracy comparable to offline systems with sub-500ms latency.

| Attribute | Value |
|-----------|-------|
| **Architecture** | ~3.4B LLM + ~0.6B causal audio encoder |
| **Parameters** | ~4B total |
| **Languages** | 13: English, Chinese, Hindi, Spanish, Arabic, French, Portuguese, Russian, German, Japanese, Korean, Italian, Dutch |
| **Latency** | Configurable 80ms-2400ms (sweet spot: 480ms) |
| **Streaming** | Native (causal encoder, sliding window attention) |
| **Word timestamps** | Yes |
| **VRAM** | >=16GB minimum (~35GB practical) |
| **Throughput** | >12.5 tokens/second |

**Why it matters for Dalston:**

- First viable **multilingual streaming** model (Parakeet is English-only)
- True streaming architecture (not VAD-chunked like Whisper)
- Competitive accuracy with offline models at <500ms latency
- Open weights under Apache 2.0 license

**Proposed engine ID:** `voxtral-streaming-4b`
