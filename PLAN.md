# Riva Runtime Integration Plan

## Validation: Your Understanding is Correct

**Yes, you are right.** NeMo is a training/research framework, and while it *can* do inference, it's not
optimized for production serving. Here's the key distinction:

| Aspect | NeMo (current) | Riva Runtime |
|--------|----------------|--------------|
| **Purpose** | Training, fine-tuning, research | Production inference |
| **Optimization** | PyTorch native | TensorRT (2-10x faster) |
| **Container size** | ~12GB (full NeMo + PyTorch) | ~4GB (optimized runtime) |
| **Cold start** | 30-60s (load PyTorch model) | 5-10s (load TensorRT engine) |
| **Memory** | Higher (full graph + optimizer) | Lower (inference-only) |
| **Streaming** | Manual implementation needed | Built-in gRPC streaming |
| **API** | Python library calls | gRPC + HTTP microservice |

**However**, there's an important nuance: NVIDIA has since evolved Riva into **Riva NIM** (NVIDIA
Inference Microservices). NIM is the current recommended approach - it packages the entire pipeline
(model download, TensorRT optimization, serving) into a single Docker container.

## Current State in Dalston

Dalston currently has **3 ways** to run Parakeet models:

1. **`nemo` runtime** - Full NeMo framework, loads `.nemo` checkpoints via PyTorch (`engines/stt-transcribe/parakeet/`)
2. **`nemo-onnx` runtime** - ONNX-exported NeMo models, lighter but still not TensorRT (`engines/stt-transcribe/parakeet-onnx/`)
3. **`nemo` realtime** - Full NeMo framework for streaming (`engines/stt-rt/parakeet/`)

All three load models directly into the engine process. This is fine for development but suboptimal
for production because:
- Each engine process manages its own model lifecycle (loading, GPU memory, swapping)
- No TensorRT optimization (leaving significant performance on the table)
- Large container images with full framework dependencies
- Cold starts are slow

## Proposed Architecture: Riva NIM as a Sidecar Runtime

Instead of embedding model inference in each engine, treat Riva NIM as an **inference backend** that
Dalston engines delegate to via gRPC.

```
                    Current Architecture
                    ====================
┌────────────┐     ┌─────────────────────────────────┐
│ Redis Queue│────▶│ Parakeet Engine                  │
│            │     │  ├── NeMo Framework (12GB)       │
│            │     │  ├── PyTorch Model (6GB VRAM)    │
│            │     │  └── engine.process() → transcribe│
└────────────┘     └─────────────────────────────────┘

                    Proposed Architecture
                    =====================
┌────────────┐     ┌──────────────────────┐     ┌─────────────────────────┐
│ Redis Queue│────▶│ Riva Engine (thin)   │────▶│ Riva NIM Container      │
│            │     │  ├── gRPC client      │gRPC│  ├── TensorRT engine     │
│            │     │  └── Result mapping   │    │  ├── Triton Server       │
└────────────┘     └──────────────────────┘     │  └── Auto-optimized     │
                                                └─────────────────────────┘
```

### Key Design Decisions

**Q: Where does model conversion happen?**

With NIM, the answer is: **it doesn't need to**. Riva NIM containers come with pre-built models from NGC.
For the standard Parakeet/Canary models, you just run the NIM container and it downloads + optimizes
automatically. No nemo2riva or riva-build step needed.

For **custom/fine-tuned models**, the NIM container supports a custom deployment mode where you mount
a `.nemo` checkpoint and it performs the conversion at container startup.

**Q: Batch vs Streaming?**

Riva NIM supports both:
- **Offline** (batch): `offline_recognize()` - send full audio, get complete transcript
- **Streaming**: `streaming_recognize()` - bidirectional gRPC stream, send audio chunks, get partial results

This maps perfectly to Dalston's dual processing modes.

**Q: What about word-level timestamps?**

Riva returns word-level timestamps natively - same as NeMo but via gRPC response fields.

## Implementation Plan

### Phase 1: Add `riva` Runtime (Batch Transcription)

**New files:**
- `engines/stt-transcribe/riva/engine.py` - Thin batch engine that delegates to Riva NIM via gRPC
- `engines/stt-transcribe/riva/engine.yaml` - Engine catalog entry
- `engines/stt-transcribe/riva/Dockerfile` - Lightweight container (just gRPC client)
- `engines/stt-transcribe/riva/requirements.txt` - `nvidia-riva-client`, `dalston-engine-sdk`
- `models/parakeet-ctc-1.1b-riva.yaml` - Model catalog entry for Riva variant

**Modified files:**
- `dalston/gateway/services/hf_resolver.py` - Add `"riva"` to runtime routing (for NeMo models
  that should be served via Riva)
- `dalston/orchestrator/engine_selector.py` - Add Riva engine to selection logic
- `docker-compose.yml` - Add Riva NIM sidecar service + Riva engine service

**Engine implementation sketch:**

```python
# engines/stt-transcribe/riva/engine.py
class RivaEngine(Engine):
    """Thin transcription engine that delegates to Riva NIM via gRPC."""

    def __init__(self):
        self._riva_url = os.environ["RIVA_GRPC_URL"]  # e.g., "riva-nim:50051"
        self._auth = riva.client.Auth(uri=self._riva_url)
        self._asr_service = riva.client.ASRService(self._auth)

    def process(self, engine_input, ctx):
        audio_bytes = engine_input.audio_path.read_bytes()

        config = riva.client.RecognitionConfig(
            language_code="en-US",
            enable_automatic_punctuation=True,
            enable_word_time_offsets=True,
        )

        response = self._asr_service.offline_recognize(audio_bytes, config)

        # Map Riva response → Dalston TranscribeOutput
        return self._map_response(response)
```

**Docker Compose addition:**

```yaml
services:
  # Riva NIM inference server (runs TensorRT-optimized models)
  riva-nim:
    image: nvcr.io/nim/nvidia/parakeet-1-1b-ctc-en-us:latest
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    environment:
      - NGC_API_KEY=${NGC_API_KEY}
      - NIM_HTTP_API_PORT=9000
      - NIM_GRPC_API_PORT=50051
    ports:
      - "50051:50051"
      - "9001:9000"
    volumes:
      - nim-cache:/opt/nim/.cache
    shm_size: '8gb'

  # Thin Dalston engine that calls Riva NIM
  stt-batch-transcribe-riva:
    build: engines/stt-transcribe/riva
    environment:
      - RIVA_GRPC_URL=riva-nim:50051
      - REDIS_URL=redis://redis:6379
      - DALSTON_RUNTIME=riva
    depends_on:
      riva-nim:
        condition: service_healthy
```

### Phase 2: Add Riva Streaming (Real-time)

**New files:**
- `engines/stt-rt/riva/engine.py` - Real-time engine using Riva streaming gRPC
- `engines/stt-rt/riva/engine.yaml`
- `engines/stt-rt/riva/Dockerfile`

The real-time engine would use Riva's bidirectional streaming:

```python
class RivaRealtimeEngine(RealtimeEngine):
    def transcribe(self, audio, language, model_variant, vocabulary=None):
        # Use streaming_recognize for real-time
        streaming_config = riva.client.StreamingRecognitionConfig(
            config=riva.client.RecognitionConfig(
                language_code=language or "en-US",
                enable_word_time_offsets=True,
            ),
            interim_results=True,
        )
        # ...
```

### Phase 3: Model Registry Integration

**Key question: Should model download trigger NeMo→Riva conversion?**

**Answer: No, not needed for standard models.** Riva NIM containers handle model download and
TensorRT optimization automatically from NGC. The model registry should track Riva-served models
separately.

For **custom models** (fine-tuned NeMo checkpoints):
- Add a conversion step in the model download pipeline
- After HuggingFace download, mount the `.nemo` file into Riva NIM's custom deployment path
- The NIM container handles nemo2riva + riva-build + TensorRT optimization at startup

**Modified files:**
- `dalston/db/models.py` - Add `"riva"` to runtime enum
- `dalston/gateway/services/model_registry.py` - Add Riva model status tracking
- `models/*.yaml` - Add Riva variants for existing models

### Phase 4: Multi-Model Riva Deployment

Riva NIM containers are per-model. For supporting multiple models:

```yaml
# One NIM container per model
riva-parakeet-ctc:
  image: nvcr.io/nim/nvidia/parakeet-1-1b-ctc-en-us:latest
  # ...

riva-parakeet-rnnt-multilingual:
  image: nvcr.io/nim/nvidia/parakeet-1-1b-rnnt-multilingual-asr:latest
  # ...

riva-canary:
  image: nvcr.io/nim/nvidia/canary-1b-multilingual-asr:latest
  # ...
```

The engine selector would route to the appropriate Riva NIM instance based on model selection.

## What NOT to Change

- **Keep existing NeMo/ONNX engines** - They're useful for development without NGC access and
  for fine-tuning workflows
- **Keep model registry's HuggingFace download** - Still needed for custom models and non-Riva runtimes
- **Keep engine SDK architecture** - The thin Riva engine still uses the same Engine base class,
  Redis queues, and heartbeat system

## Prerequisites

1. **NGC API Key** - Required to pull NIM containers from `nvcr.io`
2. **GPU with Compute Capability >= 7.0** (8.0+ for pre-built TensorRT engines)
3. **~30 min first-start** - NIM container downloads and optimizes model on first run
4. **~8GB shared memory** - Required for Triton's Python backend inside NIM

## Migration Path

1. Deploy Riva NIM sidecar alongside existing NeMo engines
2. Add `riva` runtime to engine catalog and selector
3. Gradually route traffic from `nemo` → `riva` runtime
4. Monitor latency/throughput improvements
5. Eventually make `riva` the default for production GPU deployments
6. Keep `nemo` as fallback for CPU-only and development environments

## Available NIM Models (as of 2025/2026)

| Model | Container ID | Languages | Streaming |
|-------|-------------|-----------|-----------|
| Parakeet CTC 1.1B | `parakeet-1-1b-ctc-en-us` | en | Yes |
| Parakeet RNNT Multilingual | `parakeet-1-1b-rnnt-multilingual-asr` | Multi | Yes |
| Canary 1B | `canary-1b-multilingual-asr` | 26+ langs | Yes |
| Whisper Large V3 | `whisper-large-v3` | Multi | Yes |

## Summary

Your intuition is correct: NeMo models on HuggingFace are for training, Riva (now NIM) is for
optimized inference. The good news is that integrating Riva NIM into Dalston is relatively clean
because:

1. NIM containers are self-contained microservices (no conversion pipeline to manage)
2. The gRPC API maps well to Dalston's engine model
3. Both batch and streaming are supported
4. Existing engines can coexist alongside Riva during migration
