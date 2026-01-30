# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Dalston is a modular, self-hosted audio transcription server that provides both batch and real-time transcription with an ElevenLabs-compatible API. The system uses containerized engines with Redis queues for batch processing and direct WebSocket connections for real-time transcription.

## Architecture

The system is composed of several key components:

- **Gateway**: FastAPI server handling REST API + WebSocket endpoints (ports 8000)
- **Orchestrator**: Batch job scheduling and task DAG management
- **Session Router**: Real-time worker pool management and session allocation
- **Redis**: State storage, queues, pub/sub coordination (port 6379)
- **Batch Engines**: Containerized processors (transcribe, align, diarize, refine, merge)
- **Real-time Workers**: WebSocket servers for streaming transcription

Pipeline stages: `PREPARE → TRANSCRIBE → ALIGN → DIARIZE → DETECT → REFINE → MERGE`

## Commands

### Development Setup
```bash
# Start Redis
docker run -d -p 6379:6379 redis:7-alpine

# Install dependencies (if Python package exists)
pip install -e ".[gateway,orchestrator,session-router,dev]"

# Start Gateway (development)
uvicorn dalston.gateway.main:app --reload --host 0.0.0.0 --port 8000

# Start Orchestrator (development)
python -m dalston.orchestrator.main

# Start Real-time engine (development)
cd engines/realtime/whisper-streaming
WORKER_ID=dev-worker REDIS_URL=redis://localhost:6379 python engine.py
```

### Docker Compose Operations
```bash
# Start all services
docker compose up -d

# Start core services only (minimal setup with word timestamps)
docker compose up -d gateway orchestrator redis postgres minio minio-init \
  engine-audio-prepare engine-faster-whisper engine-whisperx-align engine-final-merger

# Start without word-level alignment (faster, smaller setup)
# Note: Submit jobs with timestamps_granularity=segment to skip alignment
docker compose up -d gateway orchestrator redis postgres minio minio-init \
  engine-audio-prepare engine-faster-whisper engine-final-merger

# Start with real-time workers
docker compose up -d gateway orchestrator redis \
  engine-faster-whisper engine-merger \
  realtime-whisper-1 realtime-whisper-2

# Scale engines for high load
docker compose up -d --scale engine-faster-whisper=2 --scale engine-pyannote=2

# View logs
docker compose logs -f gateway
docker compose logs -f engine-faster-whisper

# Stop services
docker compose down

# Rebuild specific service
docker compose build engine-faster-whisper
docker compose up -d --build engine-faster-whisper
```

### Testing
```bash
# All tests
pytest

# Batch-specific tests
pytest tests/unit/test_dag.py tests/integration/test_batch_api.py

# Real-time specific tests
pytest tests/unit/test_vad.py tests/integration/test_realtime_api.py

# With coverage
pytest --cov=dalston --cov-report=html
```

### Health Checks
```bash
# Gateway health
curl http://localhost:8000/health

# System status
curl http://localhost:8000/v1/system/status

# Redis connectivity
docker compose exec redis redis-cli ping

# Check queue depths
docker compose exec redis redis-cli LLEN dalston:queue:faster-whisper
```

## Development Workflow

### Adding New Engines
1. Create directory: `engines/{stage}/{engine-id}/`
2. Add files: `Dockerfile`, `requirements.txt`, `engine.yaml`, `engine.py`
3. Implement `Engine.process()` method using dalston-engine-sdk
4. Add service definition to docker-compose.yml
5. Test with minimal engine setup

### API Compatibility
- **Dalston Native**: `/v1/audio/transcriptions/*`
- **ElevenLabs Compatible**: `/v1/speech-to-text/*`
- **WebSocket Real-time**: `/v1/audio/transcriptions/stream` (Dalston) or `/v1/speech-to-text/realtime` (ElevenLabs)

### File Structure
- `dalston/gateway/` - FastAPI REST + WebSocket API server
- `dalston/orchestrator/` - Batch job DAG scheduling
- `dalston/session_router/` - Real-time worker pool management
- `dalston/engine_sdk/` - SDK for batch engines (Redis queue-based)
- `dalston/realtime_sdk/` - SDK for real-time engines (WebSocket-based)
- `engines/` - Engine implementations organized by stage
- `web/` - React management console (Vite + TypeScript)
- `docker/` - Dockerfiles for core services
- `docs/` - Comprehensive architecture and API documentation

## Configuration

### Required Environment Variables
```bash
# HuggingFace token (required for pyannote diarization)
HF_TOKEN=hf_xxxxxxxxxxxxxxxxxxxxx

# LLM API keys (optional, for llm-cleanup engine)
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxxxxxxxxxxx
OPENAI_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxx

# Redis connection
REDIS_URL=redis://localhost:6379

# Real-time settings
REALTIME_MAX_SESSIONS_PER_WORKER=4
```

### GPU Requirements
Most transcription and diarization engines require NVIDIA GPU with CUDA. CPU-only engines include audio-prepare, final-merger, and llm-cleanup.

## Key Design Patterns

### Dual Processing Modes
- **Batch**: File upload → task DAG → queue-based processing → results
- **Real-time**: WebSocket stream → direct worker connection → streaming results
- **Hybrid**: Real-time for immediate results + batch enhancement for speaker ID and cleanup

### Engine Types
- **Single-stage**: One processing step (transcribe, align, diarize, etc.)
- **Multi-stage**: Integrated pipeline (e.g., whisperx-full does transcribe+align+diarize)
- **Batch engines**: Redis queue polling, file I/O
- **Real-time engines**: WebSocket servers, streaming audio processing

### Data Flow
- Batch: `Gateway → Orchestrator → Redis Queues → Engines → Shared Filesystem`
- Real-time: `Gateway → Session Router → Direct WebSocket → Real-time Workers`