# M63: Riva NIM Runtime

|                  |                                                                                         |
| ---------------- | --------------------------------------------------------------------------------------- |
| **Goal**         | Dalston delegates transcription inference to NVIDIA Riva NIM containers for TensorRT-optimized, production-grade throughput on GPU |
| **Duration**     | Phase 1: 3 days; Phase 2: 3 days; Phase 3: 2 days; Phase 4: 2 days                     |
| **Dependencies** | M30 (Engine Metadata), M31 (Capability-Driven Routing), M36 (Runtime Model Management)  |
| **Deliverable**  | `riva` runtime engines (batch + realtime) that delegate to a Riva NIM sidecar via gRPC, with model catalog entries, docker-compose services, and end-to-end tests |
| **Status**       | Not started                                                                              |

## User Story

> *"As an operator, I want to deploy Riva NIM containers as GPU inference backends so transcription runs 2-10x faster with TensorRT optimization, without changing the Dalston API surface."*

> *"As a developer, I want to keep using the same `/v1/audio/transcriptions` endpoint and get the same response format whether Dalston routes to the NeMo runtime or the Riva runtime."*

> *"As an operator on a dev machine without NGC access, I want to keep running NeMo engines directly — Riva is an optional production optimization, not a requirement."*

---

## Context

Dalston currently runs Parakeet models by loading them directly into engine processes via NeMo (PyTorch) or ONNX. This works but leaves performance on the table:

| Aspect | NeMo (current) | Riva NIM (proposed) |
|--------|----------------|---------------------|
| **Optimization** | PyTorch native | TensorRT (2-10x faster) |
| **Container size** | ~12GB (full NeMo + PyTorch) | ~4GB (optimized runtime) |
| **Cold start** | 30-60s (load PyTorch model) | 5-10s (load TensorRT engine) |
| **Memory** | Higher (full graph + optimizer) | Lower (inference-only) |
| **Streaming** | Custom implementation | Built-in gRPC streaming |
| **GPU utilization** | Single-request inference | Triton dynamic batching |

NVIDIA has evolved Riva into **Riva NIM** (NVIDIA Inference Microservices). NIM packages the full pipeline — model download, TensorRT optimization, and Triton-based serving — into a single Docker container. For standard NGC models (Parakeet, Canary, Whisper), no manual conversion pipeline is needed.

### Architecture After M63

```
                    Current (NeMo runtime)
                    =============================
┌────────────┐     ┌─────────────────────────────────┐
│ Redis Queue│────▶│ Parakeet Engine                  │
│            │     │  ├── NeMo Framework (12GB)       │
│            │     │  ├── PyTorch Model (6GB VRAM)    │
│            │     │  └── engine.process() → inference│
└────────────┘     └─────────────────────────────────┘

                    Proposed (Riva NIM runtime)
                    =============================
┌────────────┐     ┌──────────────────────┐     ┌─────────────────────────┐
│ Redis Queue│────▶│ Riva Engine (thin)   │────▶│ Riva NIM Container      │
│            │     │  ├── gRPC client      │gRPC│  ├── TensorRT engine     │
│            │     │  └── Result mapping   │    │  ├── Triton Server       │
└────────────┘     └──────────────────────┘     │  └── Auto-optimized     │
                                                └─────────────────────────┘

                    Realtime (Riva NIM streaming)
                    =============================
┌──────────┐     ┌──────────────────────┐     ┌─────────────────────────┐
│ WebSocket│────▶│ Riva RT Engine       │────▶│ Riva NIM Container      │
│ (client) │     │  ├── Audio framing    │gRPC│  ├── Streaming ASR       │
│          │◀────│  └── Result relay     │◀───│  ├── Bidirectional gRPC  │
└──────────┘     └──────────────────────┘     │  └── Interim results     │
                                              └─────────────────────────┘
```

The thin Riva engine is a **gRPC client**, not an inference engine. It uses the same `Engine` / `RealtimeEngine` base classes and Redis queues as every other Dalston engine, but delegates actual model inference to the NIM container over gRPC.

---

## What We Are Doing

1. Adding a `riva` batch engine under `engines/stt-transcribe/riva/` — a thin gRPC client that wraps Riva NIM's `offline_recognize` API and maps results to Dalston's `TranscribeOutput`
2. Adding a `riva` realtime engine under `engines/stt-rt/riva/` — a thin gRPC streaming client that wraps Riva NIM's `streaming_recognize` and relays interim/final results
3. Adding Riva NIM sidecar services to docker-compose for GPU deployments
4. Adding model catalog entries for Riva-served model variants
5. Extending the model registry to support "externally managed" models that are always ready (NIM manages its own model lifecycle)
6. Wiring `riva` runtime into engine selection so the orchestrator and session router can route to it

## What We Are NOT Doing

- **Not replacing NeMo/ONNX engines** — they remain for development, CPU-only, and fine-tuning workflows. Riva is an optional production optimization.
- **Not building a model conversion pipeline** — NIM containers handle NGC model download and TensorRT optimization automatically. Custom model support (mounting `.nemo` checkpoints) is deferred.
- **Not managing Riva NIM lifecycle from Dalston** — the NIM container is a standalone service managed by docker-compose or Kubernetes. Dalston treats it as an external dependency, same as Redis or PostgreSQL.
- **Not adding NGC model browsing or download UI** — model catalog entries are static YAML files. NGC integration for discovery is out of scope.
- **Not supporting multi-GPU NIM deployment** — single NIM container per model. Multi-GPU and multi-model routing is deferred to M64+.
- **Not changing the Dalston API surface** — the REST and WebSocket APIs remain identical. Runtime selection is internal.

---

## Key Architectural Decision: External Runtime Model Readiness

**Problem:** The engine selector (`engine_selector.py`) requires `status="ready"` models in the registry for model-backed stages. The model pull flow is HuggingFace-only (`snapshot_download`). Riva NIM manages its own models externally — there is nothing to download into Dalston's model cache. If Riva models are seeded as `not_downloaded` (the current YAML seeding default), the selector will never pick them (`NoDownloadedModelError`).

Additionally, `_resolve_runtime_model_id()` for the `transcribe` stage returns `model.source` (intended for S3-backed artifact lookup). For Riva models, `source` would be the NGC container path (`nvcr.io/nim/nvidia/...`), not the runtime model tag the engine needs.

**Solution:** Introduce an `external` model management mode for models whose lifecycle is managed outside Dalston:

1. **Model YAML** — Add `management: external` field. The YAML loader seeds these models with `status="ready"` instead of `not_downloaded`.
2. **Model registry** — Skip download for `management: external` models. The "pull" endpoint returns immediately or rejects with a clear message.
3. **Runtime model ID resolution** — For `management: external` models, `_resolve_runtime_model_id()` returns `runtime_model_id` directly (the NIM model tag), not `source`.
4. **Health gating** — The Riva engine's heartbeat can report NIM availability. If the NIM container is down, the engine goes unhealthy and the selector skips it naturally.

This approach requires changes to the model registry and engine selector, but keeps the external concept generic — it will also work for future external runtimes (e.g., hosted API backends).

---

## Strategy

### Phase 1: Batch Riva Engine (Days 1-3)

Build the thin batch engine, add docker-compose services, and verify end-to-end with a real NIM container.

1. Extend model registry to support `management: external` models (always-ready, skip download)
2. Fix `_resolve_runtime_model_id()` for external models
3. Create `engines/stt-transcribe/riva/` with engine.py, engine.yaml, Dockerfile, requirements.txt
4. Implement `RivaEngine.process()` — gRPC `offline_recognize` call + result mapping
5. Add `riva-nim` sidecar and `stt-batch-transcribe-riva` services to docker-compose
6. Add model catalog entry `parakeet-ctc-1.1b-riva.yaml`

### Phase 2: Realtime Riva Engine (Days 4-6)

Build the streaming engine using Riva's bidirectional gRPC, verify with WebSocket end-to-end.

1. Create `engines/stt-rt/riva/` with engine.py, engine.yaml, Dockerfile, requirements.txt
2. Implement `load_models()` (gRPC channel setup) and `transcribe()` (gRPC streaming bridge)
3. Add `stt-rt-riva` service to docker-compose
4. Wire into session router for realtime session allocation

### Phase 3: Testing & Observability (Days 7-8)

Unit tests, integration tests, health checks, and structured logging for the new runtime.

1. Unit tests for gRPC result mapping and error handling
2. Integration tests with NIM container (GPU-only CI or manual)
3. Health check endpoint that verifies NIM container connectivity
4. Structured logging for gRPC call latency, errors, and model info

### Phase 4: Documentation & Deployment (Days 9-10)

Operator docs, Makefile targets, AWS integration, and deployment validation.

1. Add `make dev-riva` target for GPU deployments with NIM
2. Document NGC API key setup and first-start warmup
3. Add Riva runtime to `make health` checks
4. Update AWS deployment scripts and compose overlay for Riva NIM
5. Add `make aws-start-riva` target and update `dalston-aws` script
6. Validate end-to-end with production-like deployment (local + AWS)

---

## Tactical Plan

### 63.1: Support External Model Management in Registry

The engine selector requires `status="ready"` models. Riva NIM manages its own models — Dalston has nothing to download. Add an `external` management mode so these models are seeded as ready and bypass the HuggingFace download flow.

**Model YAML loader changes:**

```python
# dalston/gateway/services/model_yaml_loader.py
@dataclass
class ModelYamlEntry:
    # ... existing fields ...
    management: str = "dalston"  # "dalston" (default, HF download) or "external"
```

**Model registry seeding changes:**

```python
# dalston/gateway/services/model_registry.py — seed_from_yaml_directory()
model = ModelRegistryModel(
    # ... existing fields ...
    status="ready" if entry.management == "external" else "not_downloaded",
)
```

**Download endpoint guard:**

```python
# dalston/gateway/services/model_registry.py — pull_model()
if model.management == "external":
    raise ValueError(
        f"Model '{model_id}' is externally managed (Riva NIM). "
        f"Model lifecycle is handled by the NIM container, not Dalston."
    )
```

**Runtime model ID resolution fix:**

```python
# dalston/orchestrator/engine_selector.py
def _resolve_runtime_model_id(model: ModelRegistryModel, stage: str) -> str:
    # External models always use runtime_model_id directly
    if model.management == "external":
        return model.runtime_model_id

    # Existing behavior: transcribe uses source for S3 artifact lookup
    if stage == "transcribe":
        return model.source or model.id

    return model.runtime_model_id
```

**Files:**

- MODIFY: `dalston/gateway/services/model_yaml_loader.py` — add `management` field
- MODIFY: `dalston/gateway/services/model_registry.py` — seed as ready, guard download
- MODIFY: `dalston/orchestrator/engine_selector.py` — fix `_resolve_runtime_model_id()`
- MODIFY: `dalston/db/models.py` — add `management` column to `ModelRegistryModel`
- NEW: `alembic/versions/xxx_add_model_management_column.py`

**Tests:**

- NEW: `tests/unit/test_external_model_management.py` — seed, selection, download guard

---

### 63.2: Create Riva Batch Engine Scaffold

Create the engine directory with all required files. Follow the repo's established
Dockerfile pattern: build from repo root, copy `pyproject.toml` + `dalston/`, install
SDK via `pip install -e ".[engine-sdk]"`.

**`engines/stt-transcribe/riva/engine.yaml`:**

```yaml
schema_version: "1.1"
id: riva
runtime: riva
stage: transcribe
name: NVIDIA Riva NIM Runtime
version: 1.0.0
description: |
  Thin transcription engine that delegates inference to an NVIDIA Riva NIM
  container via gRPC. The NIM container runs TensorRT-optimized models
  served by Triton Inference Server.

container:
  gpu: none  # This engine is a gRPC client; GPU lives on the NIM container
  memory: 1G

capabilities:
  languages:
    - en
  max_audio_duration: 7200
  streaming: false
  word_timestamps: true

input:
  audio_formats:
    - wav
    - flac
  sample_rate: 16000
  channels: 1

performance:
  rtf_gpu: 0.0001  # TensorRT-optimized; ensures Riva wins RTF-based ranking
```

**`engines/stt-transcribe/riva/Dockerfile`:**

```dockerfile
# Riva NIM Transcription Engine (gRPC Client)
#
# Thin engine that delegates to an NVIDIA Riva NIM container via gRPC.
# No model files — inference happens in the NIM sidecar.
#
# Build from repo root:
#   docker compose build stt-batch-transcribe-riva
#
# Or directly:
#   docker build -t dalston/stt-batch-transcribe-riva:1.0.0 \
#     -f engines/stt-transcribe/riva/Dockerfile .

FROM python:3.11-slim

# Set working directory for dalston package
WORKDIR /opt/dalston

# Copy the dalston package source
COPY pyproject.toml .
COPY dalston/ dalston/

# Install the dalston engine SDK
RUN pip install --no-cache-dir -e ".[engine-sdk]"

# Set working directory for engine
WORKDIR /engine

# Copy engine requirements first for better caching
COPY engines/stt-transcribe/riva/requirements.txt .

# Install riva client and dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy engine implementation
COPY engines/stt-transcribe/riva/engine.py .

# Copy engine.yaml
COPY engines/stt-transcribe/riva/engine.yaml /etc/dalston/engine.yaml

CMD ["python", "engine.py"]
```

**`engines/stt-transcribe/riva/requirements.txt`:**

```
nvidia-riva-client>=2.17.0
```

**Files:**

- NEW: `engines/stt-transcribe/riva/engine.py`
- NEW: `engines/stt-transcribe/riva/engine.yaml`
- NEW: `engines/stt-transcribe/riva/Dockerfile`
- NEW: `engines/stt-transcribe/riva/requirements.txt`

---

### 63.3: Implement RivaEngine.process()

The core batch engine implementation. Reads audio from the task input, calls Riva NIM's
`offline_recognize` via gRPC, and maps the response to Dalston's `TranscribeOutput`.

The `Engine` base class has no `setup()` lifecycle hook — gRPC client initialization
happens in `__init__`. Language is read from `ctx.metadata` (via `ctx.get_metadata()`),
not a `.config` attribute.

```python
import os
import riva.client
from dalston.engine_sdk.base import Engine
from dalston.engine_sdk.types import EngineInput, EngineOutput
from dalston.engine_sdk.context import BatchTaskContext
from dalston.common.pipeline_types import (
    TranscribeOutput, Segment, Word,
    AlignmentMethod, TimestampGranularity,
)


class RivaEngine(Engine):
    """Thin transcription engine delegating to Riva NIM via gRPC."""

    def __init__(self) -> None:
        super().__init__()
        riva_url = os.environ["RIVA_GRPC_URL"]  # e.g. "riva-nim:50051"
        self._auth = riva.client.Auth(uri=riva_url)
        self._asr = riva.client.ASRService(self._auth)

    def process(self, engine_input: EngineInput, ctx: BatchTaskContext) -> EngineOutput:
        audio_bytes = engine_input.audio_path.read_bytes()
        language = ctx.get_metadata("language", "en-US")

        config = riva.client.RecognitionConfig(
            language_code=language,
            max_alternatives=1,
            enable_automatic_punctuation=True,
            enable_word_time_offsets=True,
        )

        response = self._asr.offline_recognize(audio_bytes, config)
        return self._build_output(response, ctx)

    def _build_output(self, response, ctx: BatchTaskContext) -> EngineOutput:
        segments = []
        for result in response.results:
            alt = result.alternatives[0]
            words = [
                Word(
                    text=w.word,
                    start=w.start_time,
                    end=w.end_time,
                    confidence=w.confidence,
                )
                for w in alt.words
            ]
            segments.append(Segment(
                text=alt.transcript,
                words=words,
                start=words[0].start if words else 0.0,
                end=words[-1].end if words else 0.0,
                confidence=alt.confidence,
            ))

        full_text = " ".join(s.text for s in segments)
        payload = TranscribeOutput(
            text=full_text,
            segments=segments,
            language=ctx.get_metadata("language", "en"),
            alignment_method=AlignmentMethod.NATIVE,
            timestamp_granularity_requested=TimestampGranularity.WORD,
            timestamp_granularity_actual=TimestampGranularity.WORD,
            runtime=ctx.runtime,
        )

        return EngineOutput(data=payload)
```

Key mapping decisions:
- Riva `RecognitionResult` → Dalston `Segment` (one per utterance)
- Riva `WordInfo` → Dalston `Word` (field is `text`, not `word`)
- `alignment_method = NATIVE` since Riva produces accurate timestamps
- `timestamp_granularity_requested/actual = WORD` (not the nonexistent `timestamp_granularity`)
- `runtime` is a required field on `TranscribeOutput`, sourced from `ctx.runtime`

**Files:**

- MODIFY: `engines/stt-transcribe/riva/engine.py`

**Tests:**

- NEW: `tests/unit/engines/test_riva_batch_engine.py` — mock gRPC responses, verify mapping

---

### 63.4: Add Model Catalog Entry for Riva

Create a model catalog YAML that references the `riva` runtime with `management: external`.

The `source` field is informational (NGC container path) — it is NOT used for runtime
model ID resolution because `management: external` causes `_resolve_runtime_model_id()`
to return `runtime_model_id` directly.

```yaml
# models/parakeet-ctc-1.1b-riva.yaml
schema_version: "1.1"
id: nvidia/parakeet-ctc-1.1b-riva
runtime: riva
runtime_model_id: "parakeet-1-1b-ctc-en-us"  # NIM model tag, resolved directly
management: external  # NIM manages model lifecycle; seed as ready, skip download

name: NVIDIA Parakeet CTC 1.1B (Riva NIM)
source: nvcr.io/nim/nvidia/parakeet-1-1b-ctc-en-us  # Informational only
size_gb: 4.0
stage: transcribe

description: |
  Parakeet CTC 1.1B served via NVIDIA Riva NIM with TensorRT optimization.
  Same model as nvidia/parakeet-ctc-1.1b but 2-10x faster inference.
  Requires NGC API key and NVIDIA GPU with compute capability >= 7.0.

languages:
  - en

capabilities:
  word_timestamps: true
  punctuation: true
  capitalization: true
  streaming: false
  max_audio_duration: 7200

hardware:
  min_vram_gb: 4
  supports_cpu: false
  min_ram_gb: 8

performance:
  rtf_gpu: 0.0001  # TensorRT-optimized
  rtf_cpu: null
```

**Files:**

- NEW: `models/parakeet-ctc-1.1b-riva.yaml`

---

### 63.5: Add Riva NIM Sidecar to Docker Compose

Add the NIM container as a sidecar service and the thin Riva engine.

```yaml
services:
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
    volumes:
      - nim-cache:/opt/nim/.cache
    shm_size: '8gb'
    healthcheck:
      test: ["CMD", "grpc_health_probe", "-addr=:50051"]
      interval: 15s
      timeout: 5s
      retries: 40  # First start can take ~30min for TensorRT build
      start_period: 300s

  stt-batch-transcribe-riva:
    build:
      context: .
      dockerfile: engines/stt-transcribe/riva/Dockerfile
    environment:
      - RIVA_GRPC_URL=riva-nim:50051
      - REDIS_URL=redis://redis:6379
      - DALSTON_RUNTIME=riva
    depends_on:
      riva-nim:
        condition: service_healthy
```

This service should be added to a GPU-specific compose override file (e.g., `docker-compose.riva.yml`) rather than the base compose, since it requires NGC access and GPU.

**Files:**

- NEW: `docker-compose.riva.yml` (GPU + NIM overlay)
- MODIFY: `Makefile` — add `dev-riva` target

---

### 63.6: Wire Riva Runtime into Engine Selection

The capability-driven engine selector (M31) already routes by runtime and capabilities. The `riva` engine registers via heartbeat with `runtime: riva` and its declared capabilities.

With 63.1 in place (`management: external` → seeded as `ready`, correct `runtime_model_id` resolution), the selector will find the Riva model and route to it naturally. Verify that:

- `riva` engine appears in registry when running
- Engine selector considers it alongside `nemo` and `nemo-onnx` for the `transcribe` stage
- `riva` is preferred when both are running (faster RTF score wins in ranking)
- `_resolve_runtime_model_id()` returns `"parakeet-1-1b-ctc-en-us"` (the NIM tag), not the NGC URL

**Files:**

- Possibly MODIFY: `dalston/orchestrator/engine_selector.py` — only if RTF-based ranking needs adjustment for Riva's much lower RTF

**Tests:**

- NEW: `tests/unit/test_engine_selector_riva.py` — verify Riva model selected, correct runtime_model_id resolved

---

### 63.7: Create Riva Realtime Engine Scaffold

Create the realtime engine directory. The engine must implement the `RealtimeEngine`
abstract interface: `load_models()` for startup initialization and `transcribe(audio,
language, model_variant, vocabulary)` for per-utterance inference.

**`engines/stt-rt/riva/engine.yaml`:**

```yaml
schema_version: "1.1"
id: stt-rt-riva
runtime: riva
stage: transcribe
mode: realtime
name: Riva NIM Realtime
version: 1.0.0
description: |
  Real-time streaming transcription via NVIDIA Riva NIM gRPC streaming.
  Relays audio chunks to Riva's bidirectional streaming_recognize and
  forwards interim/final results back to the client.

container:
  gpu: none  # GPU lives on the NIM container
  memory: 1G

capabilities:
  languages:
    - en
  streaming: true
  word_timestamps: true
  max_concurrency: 8
  supports_vocabulary: false

input:
  audio_formats:
    - pcm_s16le
  sample_rate: 16000
  channels: 1
```

**`engines/stt-rt/riva/Dockerfile`:**

```dockerfile
# Riva NIM Realtime Transcription Engine (gRPC Streaming Client)
#
# Build from repo root:
#   docker compose build stt-rt-riva

FROM python:3.11-slim

WORKDIR /opt/dalston
COPY pyproject.toml .
COPY dalston/ dalston/
RUN pip install --no-cache-dir -e ".[realtime-sdk]"

WORKDIR /engine
COPY engines/stt-rt/riva/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY engines/stt-rt/riva/engine.py .
COPY engines/stt-rt/riva/engine.yaml /etc/dalston/engine.yaml

CMD ["python", "engine.py"]
```

**Files:**

- NEW: `engines/stt-rt/riva/engine.py`
- NEW: `engines/stt-rt/riva/engine.yaml`
- NEW: `engines/stt-rt/riva/Dockerfile`
- NEW: `engines/stt-rt/riva/requirements.txt`

---

### 63.8: Implement Riva Realtime Engine

The realtime engine must implement the `RealtimeEngine` abstract methods:
- `load_models()` — called once at startup, sets up gRPC channel to NIM
- `transcribe(audio, language, model_variant, vocabulary)` — called per utterance by `SessionHandler` when VAD detects an endpoint

```python
import os
import numpy as np
import riva.client
from dalston.realtime_sdk.base import RealtimeEngine, TranscribeResult
from dalston.realtime_sdk.assembler import Word


class RivaRealtimeEngine(RealtimeEngine):
    """Realtime engine delegating to Riva NIM gRPC."""

    def load_models(self) -> None:
        """Set up gRPC channel to Riva NIM container."""
        riva_url = os.environ["RIVA_GRPC_URL"]
        self._auth = riva.client.Auth(uri=riva_url)
        self._asr = riva.client.ASRService(self._auth)

    def transcribe(
        self,
        audio: np.ndarray,
        language: str,
        model_variant: str,
        vocabulary: list[str] | None = None,
    ) -> TranscribeResult:
        """Transcribe a single utterance via Riva NIM offline_recognize.

        For per-utterance transcription (VAD-segmented chunks), we use
        offline_recognize rather than streaming. The SessionHandler already
        handles VAD and chunking — we just need to transcribe each chunk.
        """
        # Convert float32 numpy array to int16 PCM bytes for Riva
        audio_int16 = (audio * 32768).astype(np.int16)
        audio_bytes = audio_int16.tobytes()

        config = riva.client.RecognitionConfig(
            language_code=language if language != "auto" else "en-US",
            enable_word_time_offsets=True,
            enable_automatic_punctuation=True,
        )

        response = self._asr.offline_recognize(audio_bytes, config)

        # Map Riva response → Dalston TranscribeResult
        text_parts = []
        words = []
        for result in response.results:
            if not result.alternatives:
                continue
            alt = result.alternatives[0]
            text_parts.append(alt.transcript)
            for w in alt.words:
                words.append(Word(
                    word=w.word,
                    start=w.start_time,
                    end=w.end_time,
                    confidence=w.confidence,
                ))

        return TranscribeResult(
            text=" ".join(text_parts),
            words=words,
            language=language if language != "auto" else "en",
            confidence=response.results[0].alternatives[0].confidence
            if response.results else 0.0,
        )
```

Key design decisions:
- Uses `offline_recognize` per VAD-segmented utterance (same pattern as faster-whisper realtime engine)
- The `SessionHandler` already manages VAD, chunking, and WebSocket relay — the engine only does inference
- `load_models()` sets up the gRPC channel, no actual model loading (NIM handles that)
- Audio conversion: `RealtimeEngine` receives float32 numpy arrays; Riva expects int16 PCM bytes

**Files:**

- MODIFY: `engines/stt-rt/riva/engine.py`

**Tests:**

- NEW: `tests/unit/engines/test_riva_realtime_engine.py` — mock gRPC, verify TranscribeResult mapping

---

### 63.9: Add Realtime Riva Service to Docker Compose

Uses `DALSTON_INSTANCE` and `DALSTON_WORKER_PORT` (the actual env vars from `RealtimeEngine.__init__`), not `DALSTON_WORKER_ID`.

```yaml
# In docker-compose.riva.yml
  stt-rt-riva:
    build:
      context: .
      dockerfile: engines/stt-rt/riva/Dockerfile
    environment:
      - RIVA_GRPC_URL=riva-nim:50051
      - REDIS_URL=redis://redis:6379
      - DALSTON_INSTANCE=riva-rt-1
      - DALSTON_WORKER_PORT=9000
    depends_on:
      riva-nim:
        condition: service_healthy
```

**Files:**

- MODIFY: `docker-compose.riva.yml`

---

### 63.10: Unit Tests for gRPC Result Mapping

Test the mapping layer in isolation with mock gRPC responses. These tests run without a NIM container.

Cover:
- Single-result response → single segment
- Multi-result response → multiple segments
- Empty transcript handling
- Word timestamp extraction and ordering
- Confidence score mapping
- Language code normalization (Riva uses `en-US`, Dalston uses `en`)
- gRPC error code mapping to Dalston exceptions (`UNAVAILABLE` → retry, `INVALID_ARGUMENT` → fail fast)

**Files:**

- NEW: `tests/unit/engines/test_riva_batch_engine.py`
- NEW: `tests/unit/engines/test_riva_realtime_engine.py`

---

### 63.11: Integration Tests with NIM Container

End-to-end tests that require a running NIM container. These are GPU-only and must be
excluded from default `make test` runs.

Cover:
- Batch: submit WAV file via REST API → get transcript with word timestamps
- Realtime: open WebSocket → stream audio → receive interim + final results
- Health check: verify NIM container reachable and model loaded
- Error cases: NIM container down → graceful error, not hang

**pytest marker setup required:** The current `pyproject.toml` only defines the `e2e`
marker and excludes `not e2e`. Add a `gpu` marker and exclude it from default runs:

```toml
# pyproject.toml
[tool.pytest.ini_options]
markers = [
    "e2e: end-to-end tests requiring live Docker stack",
    "gpu: tests requiring NVIDIA GPU and NIM container",
]
addopts = "-m 'not e2e and not gpu'"
```

Mark integration tests with `@pytest.mark.gpu`. Run them explicitly with
`pytest -m gpu` on GPU-equipped machines.

**Files:**

- NEW: `tests/integration/test_riva_batch.py`
- NEW: `tests/integration/test_riva_realtime.py`
- MODIFY: `pyproject.toml` — add `gpu` marker and exclude from default runs

---

### 63.12: Health Check and Observability

Add a health probe for the Riva gRPC connection, and structured logging for inference calls.

- Engine `__init__`: verify gRPC channel connectivity before runner starts accepting tasks
- Per-request: log gRPC call duration, audio duration, RTF, model info
- On error: log gRPC status code, details, and whether retry is appropriate
- Expose NIM model info (from gRPC server reflection or health response) in engine heartbeat metadata

**Files:**

- MODIFY: `engines/stt-transcribe/riva/engine.py` — health probe in `__init__()`
- MODIFY: `engines/stt-rt/riva/engine.py` — health probe in `load_models()`

---

### 63.13: Makefile Targets and Operator Docs

Add convenience targets for Riva deployments.

```makefile
dev-riva:  ## Start full stack with Riva NIM (requires GPU + NGC_API_KEY)
    docker compose -f docker-compose.yml -f docker-compose.riva.yml up -d

stop-riva:  ## Stop Riva NIM services
    docker compose -f docker-compose.yml -f docker-compose.riva.yml down
```

**Files:**

- MODIFY: `Makefile`
- MODIFY: `docs/` — add Riva deployment section to operator guide

---

### 63.14: AWS Deployment Updates

Extend the AWS deployment scripts and compose overlay to support Riva NIM.

**`infra/docker/docker-compose.aws.yml`** — add Riva NIM sidecar and thin engine services, matching the pattern of existing AWS service overrides (S3 env vars, IAM role, `/data` volume mounts). The NIM cache volume maps to `/data/nim-cache` for persistence across instance stop/start cycles.

**`infra/scripts/dalston-aws`** — add `--riva` flag to `setup --gpu` that:
- Prompts for `NGC_API_KEY` and writes it to the instance env file
- Pulls the NIM container image during provisioning (avoids ~30min first-request delay)
- Sets `shm_size` via compose override

**`infra/scripts/user-data.sh`** — if `NGC_API_KEY` is present in env, pre-pull the NIM container during instance bootstrap. Create `/data/nim-cache` directory with correct permissions.

**Makefile** — add AWS + Riva composite targets:

```makefile
aws-start-riva:  ## Start AWS stack with Riva NIM (requires GPU + NGC_API_KEY)
    docker compose -f docker-compose.yml \
        -f infra/docker/docker-compose.aws.yml \
        -f docker-compose.riva.yml \
        --env-file .env.aws up -d

aws-stop-riva:  ## Stop AWS stack with Riva NIM
    docker compose -f docker-compose.yml \
        -f infra/docker/docker-compose.aws.yml \
        -f docker-compose.riva.yml \
        --env-file .env.aws down
```

**Docs** — update `docs/guides/aws-deploy.md` and `docs/guides/aws-deployment-scenarios.md`:
- Add "Scenario: GPU with Riva NIM" section
- Document NGC API key setup (in `.env.aws` or `dalston-aws --riva`)
- Note first-start warmup (~30min) and subsequent starts (~30s)
- Cost impact: none beyond existing GPU instance cost (NIM is a software layer, not an AWS service)

**What does NOT change:**
- Terraform modules — same EC2/S3/IAM resources, no new AWS services
- IAM policies — NIM pulls from NGC (not ECR), no AWS permission changes needed
- S3 configuration — Riva engines write results through the same Dalston pipeline

**Files:**

- MODIFY: `infra/docker/docker-compose.aws.yml`
- MODIFY: `infra/scripts/dalston-aws`
- MODIFY: `infra/scripts/user-data.sh`
- MODIFY: `Makefile`
- MODIFY: `docs/guides/aws-deploy.md`
- MODIFY: `docs/guides/aws-deployment-scenarios.md`

---

## Verification

```bash
# 1. Start with Riva NIM (requires GPU + NGC_API_KEY)
export NGC_API_KEY=...
make dev-riva

# 2. Wait for NIM warmup (first start ~30min, subsequent ~30s)
docker compose -f docker-compose.yml -f docker-compose.riva.yml logs -f riva-nim

# 3. Verify engine registered
curl -s http://localhost:8000/v1/engines | jq '.[] | select(.runtime=="riva")'

# 4. Verify model seeded as ready (not not_downloaded)
curl -s http://localhost:8000/v1/models | jq '.[] | select(.runtime=="riva")'
# Should show status: "ready", management: "external"

# 5. Batch transcription via Riva
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@test.wav" -F "language=en"
# Response includes word-level timestamps, engine_id shows riva

# 6. Realtime transcription via Riva
wscat -c ws://localhost:8000/v1/audio/transcriptions/stream
# Send audio frames, receive interim/final results

# 7. Verify NeMo engines still work (no regression)
docker compose stop stt-batch-transcribe-riva
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@test.wav" -F "language=en"
# Falls back to NeMo engine

# 8. Verify download guard for external models
curl -X POST http://localhost:8000/v1/models/nvidia/parakeet-ctc-1.1b-riva/pull
# Should return error: "externally managed, NIM handles lifecycle"
```

---

## Checkpoint

- [ ] `management: external` model support in registry (seed as ready, skip download, resolve runtime_model_id)
- [ ] `engines/stt-transcribe/riva/` created with engine.py, engine.yaml, Dockerfile, requirements.txt
- [ ] `RivaEngine.process()` calls Riva NIM `offline_recognize` and maps to `TranscribeOutput`
- [ ] `models/parakeet-ctc-1.1b-riva.yaml` catalog entry with `management: external`
- [ ] `docker-compose.riva.yml` with NIM sidecar and batch engine services
- [ ] Engine selector routes to `riva` when available (higher RTF ranking)
- [ ] `engines/stt-rt/riva/` created with `load_models()` + `transcribe()` implementation
- [ ] Realtime engine relays results from Riva gRPC
- [ ] Unit tests for gRPC result mapping (no GPU required)
- [ ] `gpu` pytest marker added and excluded from default runs
- [ ] Integration tests with live NIM container (`@pytest.mark.gpu`)
- [ ] Health check verifies gRPC connectivity before accepting tasks
- [ ] `make dev-riva` target works end-to-end
- [ ] AWS deployment scripts updated (`dalston-aws --riva`, compose overlay, user-data.sh)
- [ ] `make aws-start-riva` target works end-to-end on GPU instance
- [ ] AWS deployment docs updated with Riva scenario and NGC key setup
- [ ] NeMo engines unaffected — no regression when Riva is absent

---

## Prerequisites

1. **NGC API Key** — required to pull NIM containers from `nvcr.io`
2. **GPU with Compute Capability >= 7.0** (Volta+; 8.0+ for pre-built TensorRT engines)
3. **~30 min first-start warmup** — NIM downloads model and builds TensorRT engines on first run
4. **~8GB shared memory** — required for Triton's Python backend inside NIM (`shm_size: 8gb`)
5. **`nvidia-riva-client` Python package** — gRPC stubs for Riva ASR service

## Available NIM Models

| Model | NIM Container Tag | Languages | Streaming |
|-------|-------------------|-----------|-----------|
| Parakeet CTC 1.1B | `parakeet-1-1b-ctc-en-us` | en | Yes |
| Parakeet RNNT Multilingual | `parakeet-1-1b-rnnt-multilingual-asr` | Multi | Yes |
| Canary 1B | `canary-1b-multilingual-asr` | 26+ langs | Yes |
| Whisper Large V3 | `whisper-large-v3` | Multi | Yes |

Phase 1 targets Parakeet CTC 1.1B (English). Additional models can be added as separate model catalog entries pointing to the same `riva` runtime with different `runtime_model_id` values.

## Enables Next

- **M64**: Multi-model Riva deployment — run multiple NIM containers, route by model selection
- **M65**: Custom model deployment — mount fine-tuned `.nemo` checkpoints into NIM containers
- **M66**: Riva NIM auto-scaling — scale NIM containers based on queue depth
