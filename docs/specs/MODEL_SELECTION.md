# Model Selection Specification

## Overview

This specification defines how users select transcription models in Dalston's API. The design must accommodate multiple transcription engines (faster-whisper, Parakeet, Canary-Qwen, etc.) with varying model architectures and naming conventions.

---

## Current State

### Batch API (REST)

- **Model selection**: Not exposed in API parameters
- **Default**: `large-v3` (hardcoded in orchestrator)
- **Engine override**: `engine_transcribe` parameter exists but not documented

### Real-time API (WebSocket)

- **Model selection**: `model=fast|accurate` query parameter
- **Default engine**: Parakeet (native streaming, not VAD-chunked Whisper)
- **Mapping**: `fast` → parakeet-0.6b, `accurate` → parakeet-1.1b

### Engine Level

- Each engine defines its own model options in `engine.yaml`
- faster-whisper supports: tiny, base, small, medium, large-v1/v2/v3
- Engines read model from `input.config.model`

---

## Requirements

### Functional Requirements

1. **FR-1**: Users MUST be able to select a transcription model via API parameter
2. **FR-2**: The system MUST support multiple transcription engines with different model sets
3. **FR-3**: The system MUST provide sensible defaults for users who don't specify a model
4. **FR-4**: The system MUST validate model names and return clear errors for invalid models
5. **FR-5**: The system SHOULD support convenience aliases (e.g., `fast`, `accurate`)
6. **FR-6**: The system MUST expose available models via API endpoint (`GET /v1/models`)

### Non-Functional Requirements

1. **NFR-1**: Adding new model variants within an existing engine should require only registry updates
2. **NFR-2**: Model naming should be self-documenting and consistent
3. **NFR-3**: API should follow industry conventions (similar to OpenAI, Anthropic)

---

## Design Decision: Provider-Style Model Names

### Alternatives Considered

| Approach              | Example                                     | Pros              | Cons                              |
| --------------------- | ------------------------------------------- | ----------------- | --------------------------------- |
| **Abstract tiers**    | `model="large"`                             | Simple            | Doesn't map across architectures  |
| **Engine + size**     | `engine="whisper"`, `size="large"`          | Explicit          | Two params, validation complexity |
| **HuggingFace paths** | `model="Systran/faster-whisper-large-v3"`   | Precise           | Verbose, couples to HF            |
| **Provider-style**    | `model="whisper-large-v3"`                  | Clear, extensible | Requires registry                 |

### Selected Approach: Provider-Style Names

Model identifiers follow the pattern: `{family}-{variant}`

```
whisper-large-v3      # Whisper large v3 (default)
whisper-large-v2      # Whisper large v2
whisper-base          # Whisper base
whisper-turbo         # Whisper turbo (pruned decoder)
distil-whisper        # Distilled Whisper (English-only)
parakeet-1.1b         # NVIDIA Parakeet 1.1B
parakeet-0.6b         # NVIDIA Parakeet 0.6B
canary-qwen           # NVIDIA Canary-Qwen
voxtral-4b            # Mistral Voxtral (multilingual streaming)
```

### Rationale

1. **Self-documenting**: `whisper-large-v3` is immediately understandable
2. **Extensible**: Adding `parakeet-1.1b` requires registry entry + engine implementation
3. **Implicit routing**: Model name implies which engine to use
4. **Industry standard**: Mirrors OpenAI (`gpt-4o`), Anthropic (`claude-sonnet-4-20250514`)
5. **Alias support**: Can map `fast` → `distil-whisper`, `accurate` → `whisper-large-v3`

---

## Model Registry

Central configuration mapping model IDs to engine configuration.

### Schema

```python
@dataclass
class ModelDefinition:
    id: str                    # API-facing model identifier
    engine: str                # Engine ID (matches engine.yaml)
    engine_model: str          # Model name passed to engine

    # Metadata
    name: str                  # Human-readable name
    description: str           # Brief description
    tier: str                  # "fast", "balanced", "accurate"

    # Capabilities
    languages: int | list[str] # Number of languages or explicit list
    streaming: bool            # Supports real-time streaming
    word_timestamps: bool      # Supports word-level timing

    # Resource hints
    vram_gb: float             # Approximate VRAM requirement
    speed_factor: float        # Relative speed (1.0 = baseline)
```

### Initial Registry

| Model ID | Engine | Engine Model | Tier | Languages | Streaming |
|----------|--------|--------------|------|-----------|-----------|
| `whisper-large-v3` | faster-whisper | large-v3 | accurate | 99 | No |
| `whisper-large-v2` | faster-whisper | large-v2 | accurate | 99 | No |
| `whisper-medium` | faster-whisper | medium | balanced | 99 | No |
| `whisper-small` | faster-whisper | small | fast | 99 | No |
| `whisper-base` | faster-whisper | base | fast | 99 | No |
| `whisper-tiny` | faster-whisper | tiny | fast | 99 | No |
| `distil-whisper` | faster-whisper | distil-large-v3 | fast | 1 (en) | No |
| `parakeet-110m` | parakeet | nvidia/parakeet-tdt_ctc-110m | fast | 1 (en) | Yes |
| `parakeet-0.6b` | parakeet | nvidia/parakeet-rnnt-0.6b | fast | 1 (en) | Yes |
| `parakeet-1.1b` | parakeet | nvidia/parakeet-rnnt-1.1b | balanced | 1 (en) | Yes |

### Aliases

| Alias | Resolves To (Batch) | Resolves To (Realtime) | Use Case |
|-------|---------------------|------------------------|----------|
| `fast` | `distil-whisper` | `parakeet-0.6b` | Speed-optimized |
| `accurate` | `whisper-large-v3` | `parakeet-1.1b` | Quality-optimized |
| `large` | `whisper-large-v3` | — | Backwards compat |
| `base` | `whisper-base` | — | Backwards compat |
| `parakeet` | — | `parakeet-0.6b` | Default Parakeet model |

---

## API Changes

### Batch Transcription Endpoint

```
POST /v1/audio/transcriptions
```

**New parameter:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `model` | string | `whisper-large-v3` | Model identifier or alias |

**Example request:**

```bash
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@audio.wav" \
  -F "model=whisper-base"
```

### Real-time Transcription Endpoint

```
GET /v1/audio/transcriptions/stream?model=whisper-large-v3
```

Existing `fast`/`accurate` values continue to work as aliases.

### Models List Endpoint

```
GET /v1/models
```

**Response:**

```json
{
  "object": "list",
  "data": [
    {
      "id": "whisper-large-v3",
      "object": "model",
      "created": 1699000000,
      "owned_by": "openai",
      "name": "Whisper Large v3",
      "description": "Most accurate multilingual transcription model",
      "capabilities": {
        "languages": 99,
        "streaming": false,
        "word_timestamps": true
      },
      "tier": "accurate"
    },
    ...
  ]
}
```

### Model Details Endpoint

```
GET /v1/models/{model_id}
```

Returns detailed information about a specific model.

---

## Validation & Error Handling

### Invalid Model

```json
{
  "error": {
    "type": "invalid_request_error",
    "message": "Model 'whisper-huge' not found. Available models: whisper-large-v3, whisper-base, ...",
    "param": "model"
  }
}
```

### Engine Not Available

```json
{
  "error": {
    "type": "service_unavailable",
    "message": "Model 'parakeet-1.1b' requires engine 'parakeet' which is not currently running"
  }
}
```

---

## ElevenLabs Compatibility

ElevenLabs API uses the `model_id` parameter. For compatibility:

| ElevenLabs Endpoint | Dalston Mapping |
|--------------------|-----------------|
| `/v1/speech-to-text` | Accept both `model` and `model_id` |
| Default model | Map to `whisper-large-v3` |

---

## Adding New Engines

When adding a new engine:

1. Implement engine in `engines/transcribe/{engine-id}/`
2. Add model entries to `MODEL_REGISTRY` in `dalston/common/models.py`
3. Models automatically appear in `/v1/models` endpoint

See [M22 Parakeet Engine](../plan/milestones/M22-parakeet-engine.md) for a complete example of adding a new transcription engine.

### Language-Specific Models

Support explicit language variants:

```
whisper-large-v3         # Auto-detect language
whisper-large-v3-en      # English-optimized
whisper-base-en          # English-only base
```

### Model Routing by Capability

Future: automatic model selection based on requirements:

```python
model="auto"
language="hr"           # Route to best Croatian model
streaming=True          # Route to streaming-capable model
```

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
| **VRAM** | ≥16GB minimum (~35GB practical) |
| **Throughput** | >12.5 tokens/second |

**Performance (WER at 480ms latency):**

| Benchmark | Voxtral Realtime | Voxtral Offline |
|-----------|------------------|-----------------|
| FLEURS avg (13 langs) | 8.72% | 5.90% |
| English | 4.90% | 3.32% |
| Spanish | 3.31% | 2.63% |
| Meanwhile (long-form EN) | 5.05% | 4.08% |

**Why it matters for Dalston:**

- First viable **multilingual streaming** model (Parakeet is English-only)
- True streaming architecture (not VAD-chunked like Whisper)
- Competitive accuracy with offline models at <500ms latency
- Open weights under Apache 2.0 license

**Proposed registry entry:**

```python
"voxtral-4b": ModelDefinition(
    id="voxtral-4b",
    engine="voxtral",
    engine_model="mistralai/Voxtral-Mini-4B-Realtime-2602",
    name="Voxtral Mini 4B Realtime",
    description="Multilingual streaming transcription, 13 languages, <500ms latency",
    tier="balanced",
    languages=13,
    streaming=True,
    word_timestamps=True,
    vram_gb=16.0,
    speed_factor=5.0,  # Estimated vs Whisper baseline
),
```

**Proposed aliases:**

| Alias | Resolves To | Use Case |
|-------|-------------|----------|
| `streaming` | `voxtral-4b` | Default multilingual streaming model |
| `realtime` | `voxtral-4b` | Alias for streaming |

**Integration considerations:**

1. Requires new `engines/realtime/voxtral-streaming/` engine implementation
2. WebSocket protocol compatible with existing realtime SDK
3. May need vLLM or similar for efficient inference
4. Could become default for `model=auto` when `streaming=true` and language is in supported set

---

## Open Questions

1. **Q: Should we deprecate `engine_transcribe` parameter?**
   - Recommendation: Keep for power users who want explicit engine control, but model parameter handles routing automatically

2. **Q: How to handle model+engine mismatch?**
   - If user specifies both `model="whisper-base"` and `engine_transcribe="parakeet"`, return error

3. **Q: Version pinning?**
   - Consider: `whisper-large-v3@20240101` for reproducibility
   - Defer to future iteration
