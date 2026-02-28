# Dalston Project Structure

## Directory Layout

```
dalston/
│
├── pyproject.toml                      # Project metadata and dependencies
├── docker-compose.yml                  # Container orchestration
├── .env.example                        # Environment variables template
├── README.md                           # Project overview
│
├── dalston/                            # Main Python package
│   ├── __init__.py
│   ├── config.py                       # Configuration management
│   │
│   ├── gateway/                        # API Gateway
│   │   ├── __init__.py
│   │   ├── main.py                     # FastAPI application entry point
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   ├── v1/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── router.py           # API router aggregation
│   │   │   │   ├── transcription.py    # Batch: POST/GET /v1/audio/transcriptions
│   │   │   │   ├── realtime.py         # Realtime: WS /v1/audio/transcriptions/stream
│   │   │   │   ├── realtime_status.py  # Realtime: GET /v1/realtime/*
│   │   │   │   ├── engines.py          # GET /v1/engines
│   │   │   │   └── system.py           # GET /v1/system/status
│   │   │   └── console.py              # Management API for web console
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── jobs.py                 # Batch job management
│   │   │   ├── results.py              # Result retrieval
│   │   │   ├── engines.py              # Engine registry
│   │   │   └── session_router.py       # Realtime session router client
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   ├── requests.py             # Pydantic request schemas
│   │   │   └── responses.py            # Pydantic response schemas
│   │   └── middleware/
│   │       ├── __init__.py
│   │       └── error_handler.py        # Global error handling
│   │
│   ├── orchestrator/                   # Batch Job Orchestrator
│   │   ├── __init__.py
│   │   ├── main.py                     # Orchestrator entry point
│   │   ├── dag.py                      # DAG builder from job parameters
│   │   ├── scheduler.py                # Task scheduling
│   │   ├── handlers.py                 # Event handlers
│   │   ├── engine_selector.py          # Engine selection logic
│   │   └── audio_analyzer.py           # Audio property analysis
│   │
│   ├── session_router/                 # Realtime Session Router
│   │   ├── __init__.py
│   │   ├── main.py                     # Entry point (if standalone)
│   │   ├── router.py                   # Main router class
│   │   ├── registry.py                 # Worker registry management
│   │   ├── allocator.py                # Session allocation logic
│   │   └── health.py                   # Worker health monitoring
│   │
│   ├── engine_sdk/                     # SDK for batch engines
│   │   ├── __init__.py
│   │   ├── base.py                     # Base Engine class
│   │   ├── runner.py                   # Queue polling loop
│   │   ├── io.py                       # File I/O helpers
│   │   ├── redis_client.py             # Redis wrapper
│   │   └── types.py                    # TaskInput, TaskOutput
│   │
│   ├── realtime_sdk/                   # SDK for realtime engines
│   │   ├── __init__.py
│   │   ├── engine.py                   # Base RealtimeEngine class
│   │   ├── session.py                  # Session handler base
│   │   ├── vad.py                      # Voice activity detection
│   │   ├── asr.py                      # Streaming ASR wrapper
│   │   ├── assembler.py                # Transcript assembly
│   │   ├── registry.py                 # Registry client
│   │   └── protocol.py                 # WebSocket message types
│   │
│   └── common/                         # Shared utilities
│       ├── __init__.py
│       ├── redis.py                    # Redis client factory
│       ├── models.py                   # Shared data models
│       └── utils.py                    # Common utilities
│
├── engines/                            # Engine implementations
│   │
│   ├── prepare/                        # Audio preparation
│   │   └── audio-prepare/
│   │       ├── Dockerfile
│   │       ├── requirements.txt
│   │       ├── engine.yaml
│   │       └── engine.py
│   │
│   ├── transcribe/                     # Batch transcription
│   │   ├── faster-whisper/
│   │   │   └── ...
│   │   ├── parakeet/
│   │   │   └── ...
│   │   └── whisper-openai/
│   │       └── ...
│   │
│   ├── align/                          # Word alignment
│   │   └── phoneme-align/              # Standalone CTC forced alignment
│   │       └── ...
│   │
│   ├── diarize/                        # Speaker diarization
│   │   └── pyannote-4.0/
│   │       └── ...
│   │
│   ├── detect/                         # Audio analysis
│   │   ├── emotion2vec/
│   │   │   └── ...
│   │   └── panns-events/
│   │       └── ...
│   │
│   ├── refine/                         # LLM refinement
│   │   └── llm-cleanup/
│   │       └── ...
│   │
│   ├── merge/                          # Output merging
│   │   └── final-merger/
│   │       └── ...
│   │
│   ├── multi/                          # Multi-stage batch engines
│   │   └── whisperx-full/
│   │       └── ...
│   │
│   └── realtime/                       # Realtime streaming engines
│       └── whisper-streaming/
│           ├── Dockerfile
│           ├── requirements.txt
│           ├── engine.yaml
│           └── engine.py
│
├── web/                                # React Management Console
│   ├── package.json
│   ├── package-lock.json
│   ├── vite.config.ts
│   ├── tsconfig.json
│   ├── index.html
│   └── src/
│       ├── main.tsx
│       ├── App.tsx
│       ├── api/
│       │   ├── client.ts
│       │   └── types.ts
│       ├── pages/
│       │   ├── Dashboard.tsx           # Unified batch + realtime overview
│       │   ├── BatchJobs.tsx           # Batch job list
│       │   ├── BatchJobDetail.tsx      # Batch job with DAG
│       │   ├── RealtimeSessions.tsx    # Active realtime sessions
│       │   ├── Engines.tsx             # All engines (batch + realtime)
│       │   └── Settings.tsx            # System configuration
│       ├── components/
│       │   ├── Layout.tsx
│       │   ├── Sidebar.tsx
│       │   ├── DAGViewer.tsx           # Batch task DAG
│       │   ├── TranscriptViewer.tsx
│       │   ├── AudioPlayer.tsx
│       │   ├── RealtimeMonitor.tsx     # Live session stats
│       │   ├── CapacityGauge.tsx       # Realtime capacity
│       │   ├── ProgressBar.tsx
│       │   └── StatusBadge.tsx
│       └── hooks/
│           ├── useJobs.ts
│           ├── useSessions.ts
│           └── useWebSocket.ts
│
├── docker/                             # Dockerfiles
│   ├── Dockerfile.gateway
│   ├── Dockerfile.orchestrator
│   ├── Dockerfile.session-router       # If standalone
│   └── Dockerfile.base                 # Base for batch engines
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── unit/
│   │   ├── test_dag.py
│   │   ├── test_engine_selector.py
│   │   ├── test_vad.py
│   │   ├── test_session_router.py
│   │   └── test_assembler.py
│   ├── integration/
│   │   ├── test_batch_api.py
│   │   ├── test_realtime_api.py
│   │   ├── test_job_flow.py
│   │   └── test_session_flow.py
│   └── fixtures/
│       └── audio/
│           ├── short_mono.wav
│           ├── short_stereo.wav
│           └── long_interview.wav
│
├── scripts/
│   ├── setup.sh
│   ├── download_models.sh
│   └── benchmark.py
│
└── docs/
    ├── ARCHITECTURE.md                 # Unified architecture overview
    ├── PROJECT_STRUCTURE.md            # This file
    │
    ├── batch/                          # Batch transcription docs
    │   ├── API.md
    │   ├── ORCHESTRATOR.md
    │   ├── DATA_MODEL.md
    │   ├── ENGINES.md
    │   └── DOCKER.md
    │
    └── realtime/                       # Realtime transcription docs
        ├── REALTIME.md
        ├── WEBSOCKET_API.md
        ├── SESSION_ROUTER.md
        └── REALTIME_ENGINES.md
```

---

## Package Details

### dalston/gateway/

The API Gateway serving both batch and realtime endpoints.

| File | Purpose |
|------|---------|
| `main.py` | FastAPI app, middleware, router mounting |
| `api/v1/transcription.py` | Batch transcription endpoints |
| `api/v1/realtime.py` | WebSocket endpoint, proxy to workers |
| `api/v1/realtime_status.py` | Realtime capacity and session management |
| `services/jobs.py` | Batch job lifecycle |
| `services/session_router.py` | Client for Session Router |

### dalston/orchestrator/

Batch job orchestration (unchanged from batch-only design).

| File | Purpose |
|------|---------|
| `main.py` | Entry point, event loop |
| `dag.py` | Build task DAG from parameters |
| `scheduler.py` | Task queue management |
| `handlers.py` | Event handlers |
| `engine_selector.py` | Select optimal engines |

### dalston/session_router/

Realtime worker pool management.

| File | Purpose |
|------|---------|
| `main.py` | Entry point (if standalone) |
| `router.py` | Main SessionRouter class |
| `registry.py` | Worker registration and tracking |
| `allocator.py` | Session-to-worker allocation |
| `health.py` | Heartbeat monitoring |

### dalston/engine_sdk/

SDK for batch engines (queue-based).

| File | Purpose |
|------|---------|
| `base.py` | Abstract `Engine` class |
| `runner.py` | Queue polling loop |
| `io.py` | Task input/output |
| `types.py` | `TaskInput`, `TaskOutput` |

### dalston/realtime_sdk/

SDK for realtime engines (WebSocket-based).

| File | Purpose |
|------|---------|
| `engine.py` | Base `RealtimeEngine` class |
| `session.py` | Session handler base |
| `vad.py` | Silero VAD wrapper |
| `asr.py` | Streaming ASR interface |
| `assembler.py` | Transcript assembly |
| `registry.py` | Registry client |
| `protocol.py` | Message types |

---

## Engine Directory Structure

### Batch Engine

```
engines/{stage}/{engine-id}/
├── Dockerfile
├── requirements.txt
├── engine.yaml          # Metadata: stages, GPU, config schema
└── engine.py            # Implements Engine.process()
```

### Realtime Engine

```
engines/realtime/{engine-id}/
├── Dockerfile
├── requirements.txt
├── engine.yaml          # Metadata: models, capacity, capabilities
└── engine.py            # WebSocket server, session handling
```

---

## Docker Compose Services

### Core Services

| Service | Purpose |
|---------|---------|
| `gateway` | API server (REST + WebSocket) |
| `orchestrator` | Batch job scheduling |
| `session-router` | Realtime worker management (optional if embedded) |
| `redis` | State, queues, pub/sub |

### Batch Engines

| Service | Stage |
|---------|-------|
| `stt-batch-prepare` | prepare |
| `stt-batch-transcribe-whisper-cpu` | transcribe |
| `stt-batch-transcribe-parakeet` | transcribe |
| `stt-batch-align-phoneme-cpu` | align |
| `stt-batch-diarize-pyannote-v40-cpu` | diarize |
| `stt-batch-pii-detect-presidio` | pii_detect |
| `stt-batch-audio-redact-audio` | audio_redact |
| `stt-batch-merge` | merge |

### Realtime Engines

| Service | Purpose |
|---------|---------|
| `stt-rt-transcribe-whisper-1` | Streaming transcription worker |
| `stt-rt-transcribe-whisper-2` | Streaming transcription worker |
| `stt-rt-transcribe-whisper-N` | Additional workers as needed |

---

## Configuration Files

### pyproject.toml

```toml
[project]
name = "dalston"
version = "0.1.0"
description = "Modular audio transcription server"
requires-python = ">=3.11"

[project.optional-dependencies]
gateway = [
    "fastapi>=0.109.0",
    "uvicorn>=0.27.0",
    "python-multipart>=0.0.6",
    "redis>=5.0.0",
    "websockets>=12.0",
]
orchestrator = [
    "redis>=5.0.0",
]
session-router = [
    "redis>=5.0.0",
]
engine-sdk = [
    "redis>=5.0.0",
]
realtime-sdk = [
    "redis>=5.0.0",
    "websockets>=12.0",
    "numpy>=1.24.0",
]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.23.0",
    "httpx>=0.26.0",
]
```

### .env.example

```bash
# Required
HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxxx

# Optional (for LLM cleanup)
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxxx
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxx

# Redis
REDIS_URL=redis://localhost:6379

# Realtime
REALTIME_MAX_SESSIONS_PER_WORKER=4
```

---

## Development Workflow

### Running Locally

```bash
# Install all components
pip install -e ".[gateway,orchestrator,session-router,dev]"

# Start Redis
docker run -d -p 6379:6379 redis:7-alpine

# Terminal 1: Gateway
uvicorn dalston.gateway.main:app --reload

# Terminal 2: Orchestrator (for batch)
python -m dalston.orchestrator.main

# Terminal 3: Realtime engine (for realtime)
cd engines/realtime/whisper-streaming
pip install -r requirements.txt
WORKER_ID=dev-worker REDIS_URL=redis://localhost:6379 python engine.py
```

### Testing

```bash
# All tests
pytest

# Batch tests
pytest tests/unit/test_dag.py tests/integration/test_batch_api.py

# Realtime tests
pytest tests/unit/test_vad.py tests/integration/test_realtime_api.py

# With coverage
pytest --cov=dalston --cov-report=html
```

### Building Containers

```bash
# Build all
docker compose build

# Build specific
docker compose build gateway stt-batch-transcribe-whisper-cpu stt-rt-transcribe-whisper-1

# Start batch + realtime
docker compose up -d gateway orchestrator redis \
  stt-batch-transcribe-whisper-cpu stt-batch-merge \
  stt-rt-transcribe-whisper-1 stt-rt-transcribe-whisper-2
```
