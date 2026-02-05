# M22: Parakeet Engine

| | |
|---|---|
| **Goal** | Add NVIDIA Parakeet as a batch and real-time transcription engine |
| **Duration** | 4-5 days |
| **Dependencies** | M2 (batch transcription), M6 (real-time infrastructure), M14 (model selection) |
| **Deliverable** | Parakeet batch engine, Parakeet streaming engine, model registry entries |

## User Story

> *"As a developer transcribing English audio, I want to use NVIDIA's Parakeet model for faster transcription with lower latency, especially for real-time streaming where sub-100ms response times matter."*

---

## Overview

NVIDIA Parakeet FastConformer is a family of English-optimized ASR models built on the FastConformer encoder with RNNT and CTC decoders. The 0.6B-parameter model achieves ~7.2% average WER with RTFx >2000, making it significantly faster than Whisper while maintaining competitive accuracy for English.

Key advantages over the existing Whisper-based engines:

1. **Native streaming** — Cache-aware FastConformer encoder supports true streaming inference without the chunked re-encoding Whisper requires
2. **Lower latency** — ~100ms end-to-end with cache-aware chunking vs ~200-400ms for Whisper streaming
3. **Higher throughput** — RTFx >2000 vs ~4-16x for faster-whisper variants
4. **Smaller footprint** — ~4GB VRAM vs 5-10GB for Whisper large models

Limitations:

- **English only** — No multilingual support; Whisper remains the default for non-English
- **NVIDIA GPUs only** — Requires CUDA; no CPU fallback for production use
- **No built-in diarization** — Still requires pyannote in the batch pipeline

This milestone adds Parakeet as both a batch engine (for file transcription) and a real-time engine (for WebSocket streaming), integrated into the model selection system from M14.

---

## Architecture

```text
                    ┌─────────────────────────────┐
                    │          Gateway             │
                    │                              │
                    │  POST /v1/audio/transcriptions│
                    │  model=parakeet-0.6b         │
                    │                              │
                    │  WS /v1/.../stream            │
                    │  model=parakeet-0.6b         │
                    └──────────┬──────────┬────────┘
                               │          │
                    ┌──────────▼──┐  ┌────▼─────────────┐
                    │ Orchestrator│  │  Session Router   │
                    │             │  │                   │
                    │ Routes to   │  │ Routes to         │
                    │ engine:     │  │ parakeet-streaming│
                    │ parakeet    │  │ workers           │
                    └──────┬──────┘  └────┬──────────────┘
                           │              │
              ┌────────────▼──┐  ┌────────▼──────────────┐
              │  Batch Engine  │  │  Real-time Engine      │
              │                │  │                        │
              │  engines/      │  │  engines/              │
              │  transcribe/   │  │  realtime/             │
              │  parakeet/     │  │  parakeet-streaming/   │
              │                │  │                        │
              │  NeMo RNNT     │  │  Cache-aware streaming │
              │  0.6B / 1.1B   │  │  FastConformer + RNNT  │
              └────────────────┘  └────────────────────────┘
```

### Pipeline Integration

**Batch pipeline** — Parakeet replaces the TRANSCRIBE stage only. Downstream stages (align, diarize, merge) remain unchanged:

```
PREPARE → TRANSCRIBE (parakeet) → ALIGN → DIARIZE → MERGE
```

Note: Parakeet produces word-level timestamps natively via RNNT alignment, so the ALIGN stage may be skipped when using Parakeet. The orchestrator should detect this and build the DAG accordingly.

**Real-time pipeline** — Parakeet replaces the streaming ASR component:

```
Audio Stream → VAD → Parakeet Streaming ASR → Transcript Assembly → WebSocket Output
```

---

## Steps

### 22.1: Parakeet Batch Engine

```text
engines/transcribe/parakeet/
├── Dockerfile
├── requirements.txt
├── engine.yaml
└── engine.py
```

**Deliverables:**

- `ParakeetEngine` extending `Engine` base class from `dalston/engine_sdk`
- NeMo RNNT inference for offline/batch transcription
- Support for `parakeet-rnnt-0.6b` and `parakeet-rnnt-1.1b` model variants
- Word-level timestamps from RNNT alignment
- Automatic GPU detection with CUDA requirement
- `engine.yaml` defining capabilities, config schema, and output schema
- Dockerfile with NeMo toolkit and pre-downloaded 0.6B model
- Output format compatible with existing pipeline (segments with text, start, end, words)

See [implementation plan](../impl/M22-22.1-parakeet-batch-engine.md) for details.

---

### 22.2: Parakeet Real-time Engine

```text
engines/realtime/parakeet-streaming/
├── Dockerfile
├── requirements.txt
├── engine.yaml
└── engine.py
```

**Deliverables:**

- `ParakeetStreamingEngine` extending `RealtimeEngine` base class from `dalston/realtime_sdk`
- Cache-aware streaming inference using FastConformer encoder
- Configurable chunk size for latency vs accuracy tradeoff
- Native streaming without chunked re-encoding
- Worker registration and heartbeat via realtime SDK
- Dockerfile with NeMo toolkit and streaming dependencies
- `engine.yaml` declaring streaming capabilities and latency characteristics

See [implementation plan](../impl/M22-22.2-parakeet-realtime-engine.md) for details.

---

### 22.3: Model Registry & Docker Integration

**Deliverables:**

- Add model entries to `dalston/common/models.py`:
  - `parakeet-0.6b` — Default Parakeet model (0.6B RNNT)
  - `parakeet-1.1b` — Larger Parakeet model (1.1B RNNT)
- Add model alias: `parakeet` → `parakeet-0.6b`
- Docker Compose service definitions for both batch and real-time engines
- Orchestrator DAG update: skip ALIGN stage when Parakeet is selected (native word timestamps)
- Update `GET /v1/models` response to include Parakeet models with `languages: ["en"]` constraint

**Model registry entries:**

| Model ID | Engine | Engine Model | Tier | Languages | Streaming | VRAM |
|----------|--------|-------------|------|-----------|-----------|------|
| `parakeet-0.6b` | parakeet | `nvidia/parakeet-rnnt-0.6b` | fast | 1 (en) | Yes | 4.0 |
| `parakeet-1.1b` | parakeet | `nvidia/parakeet-rnnt-1.1b` | balanced | 1 (en) | Yes | 6.0 |

**Docker Compose services:**

```yaml
# Batch engine
engine-parakeet:
  build: ./engines/transcribe/parakeet
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: 1
            capabilities: [gpu]

# Real-time engine
realtime-parakeet-1:
  build: ./engines/realtime/parakeet-streaming
  environment:
    - WORKER_ID=realtime-parakeet-1
    - WORKER_PORT=9000
    - MAX_SESSIONS=4
    - REDIS_URL=redis://redis:6379
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: 1
            capabilities: [gpu]
```

---

### 22.4: Testing & Validation

**Deliverables:**

- Unit tests for `ParakeetEngine.process()` with mocked NeMo model
- Unit tests for `ParakeetStreamingEngine.transcribe()` with mocked inference
- Integration tests for batch API flow with `model=parakeet-0.6b`
- Integration tests for real-time WebSocket with Parakeet worker
- Validation that ALIGN stage is correctly skipped in the DAG
- Validation that language constraint (`en` only) is enforced by the model registry

**Test files:**

```text
tests/
├── unit/
│   ├── test_parakeet_engine.py          # Batch engine unit tests
│   └── test_parakeet_streaming.py       # Real-time engine unit tests
└── integration/
    ├── test_parakeet_batch.py           # Batch API with Parakeet
    └── test_parakeet_realtime.py        # WebSocket with Parakeet
```

---

## Verification

### Batch Transcription

```bash
# Transcribe with Parakeet (default 0.6B)
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@test-english.wav" \
  -F "model=parakeet-0.6b"
# → {"id": "job_abc123", "status": "pending"}

# Poll for result
curl http://localhost:8000/v1/audio/transcriptions/job_abc123
# → {"status": "completed", "text": "...", "segments": [...]}
# Segments include word-level timestamps from RNNT alignment

# Verify language enforcement
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@test-french.wav" \
  -F "model=parakeet-0.6b" \
  -F "language=fr"
# → 400: "Model parakeet-0.6b only supports English"
```

### Model Discovery

```bash
# List models — Parakeet appears
curl http://localhost:8000/v1/models
# → includes {"id": "parakeet-0.6b", "name": "Parakeet 0.6B", ...}

# Model details
curl http://localhost:8000/v1/models/parakeet-0.6b
# → {"id": "parakeet-0.6b", "engine": "parakeet", "capabilities": {"languages": ["en"], "streaming": true, ...}}
```

### Real-time Streaming

```bash
# Start Parakeet real-time worker
docker compose up -d realtime-parakeet-1

# Stream with Parakeet
wscat -c "ws://localhost:8000/v1/audio/transcriptions/stream?model=parakeet-0.6b"
# → Send audio, observe lower latency transcripts compared to Whisper
```

### Health Check

```bash
# Batch engine health
docker compose logs engine-parakeet | grep "healthy"

# Real-time worker health
docker compose exec redis redis-cli HGETALL dalston:realtime:worker:realtime-parakeet-1
# → Shows status, capacity, active_sessions, models
```

---

## Checkpoint

- [ ] **Batch engine** transcribes English audio with Parakeet RNNT
- [ ] **Word timestamps** produced natively without separate ALIGN stage
- [ ] **Real-time engine** streams transcripts with sub-100ms latency
- [ ] **Model registry** includes `parakeet-0.6b` and `parakeet-1.1b`
- [ ] **Language validation** rejects non-English requests for Parakeet models
- [ ] **DAG optimization** skips ALIGN stage when using Parakeet
- [ ] **Docker Compose** includes both batch and real-time Parakeet services
- [ ] **Tests** cover batch and real-time flows with mocked NeMo models
- [ ] **Output format** compatible with downstream pipeline stages (merge, export)

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| NeMo toolkit is heavy (~2GB+ install) | Multi-stage Docker build, pre-download model at build time |
| CUDA-only inference (no CPU fallback) | Document GPU requirement clearly, fail fast with helpful error |
| RNNT streaming state management | Cache-aware chunking handled by NeMo APIs; wrap cleanly in SDK |
| Model version drift (NeMo updates) | Pin NeMo version in requirements.txt, test before upgrading |
| Output format differences from Whisper | Normalize to shared segment/word format in engine.py |

---

**Next**: This milestone can proceed independently once M2, M6, and M14 are complete. The batch engine (21.1) and real-time engine (21.2) can be developed in parallel since they share no code dependencies beyond the SDKs.
