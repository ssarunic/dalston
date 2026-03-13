# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Dalston is a modular, self-hosted audio transcription server that provides both batch and real-time transcription with an ElevenLabs-compatible API. The system uses containerized engines with Redis queues for batch processing and direct WebSocket connections for real-time transcription. Python >=3.11.

## Architecture

The system is composed of several key components:

- **Gateway**: FastAPI server handling REST API + WebSocket endpoints (ports 8000)
- **Orchestrator**: Batch job scheduling and task DAG management
- **Session Router**: Real-time worker pool management and session allocation
- **Redis**: State storage, queues, pub/sub coordination (port 6379)
- **Batch Engines**: Containerized processors (transcribe, align, diarize, refine, merge)
- **Real-time Workers**: WebSocket servers for streaming transcription

Pipeline stages: `PREPARE → TRANSCRIBE → ALIGN → DIARIZE → PII_DETECT → AUDIO_REDACT → MERGE`

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
export DALSTON_WORKER_ID=dev-worker
export REDIS_URL=redis://localhost:6379
python engine.py
```

### Local Docker Setup (via Makefile)

**IMPORTANT**: Always use Makefile commands instead of raw docker compose. Run `make help` for all available commands.

**CPU-only testing**: When testing locally on a dev machine without GPU, use `make dev` or `make dev-minimal`. GPU engines require `make dev-gpu`.

```bash
# See all available commands
make help

# Start full local stack (postgres, redis, minio, gateway, orchestrator, CPU engines)
make dev

# Start minimal stack for quick iteration
make dev-minimal

# Start with GPU engines (requires NVIDIA GPU)
make dev-gpu

# Stop all services
make stop

# View logs
make logs          # gateway only
make logs-all      # all services

# Show running services
make ps

# Rebuild and restart a specific engine
make rebuild ENGINE=stt-batch-transcribe-faster-whisper-base

# Rebuild gateway with latest web console changes
make deploy-web

# Build CPU variants (for Mac development)
make build-cpu

# Build GPU variants
make build-gpu

# Check service health
make health

# Validate compose configurations
make validate
```

### AWS Deployment

```bash
# Start on AWS with local infra + GPU
make aws-start

# Stop AWS services
make aws-stop

# Follow logs on AWS
make aws-logs
```

### Testing

```bash
# All tests
make test

# End-to-end tests (requires running Docker stack)
make test-e2e

# With coverage
make test-cov

# Run linters (ruff, mypy)
make lint

# Format code
make fmt
```

### Health Checks

```bash
# Check all services
make health

# Show system status
make status

# Show queue depths
make queues
```

### Data Preservation

Never create new API keys via `POST /auth/keys` or `AuthService.create_api_key()` during testing or debugging. Use the existing key from `.env` (`DALSTON_API_KEY`). Do not modify `.env` unless the user asks.

### Environment Management (Docker vs Local)

**CRITICAL**: Never mix Docker and local Python processes for the same service. They share Redis/Postgres and will conflict, causing subtle bugs like duplicate event processing or stuck jobs.

**Before testing with Docker (`make dev`):**

```bash
# Kill any local dalston processes first
pkill -f "dalston.orchestrator" || true
pkill -f "dalston.gateway" || true

# Verify no conflicts
ps aux | grep -E "dalston\.(orchestrator|gateway)" | grep -v grep
```

**Before testing locally (without Docker):**

```bash
# Stop Docker services that would conflict
docker compose stop orchestrator gateway
```

**Pre-flight check for debugging stuck jobs:**

```bash
# Check for duplicate orchestrators (should be exactly 1)
docker ps | grep orchestrator
ps aux | grep "dalston.orchestrator" | grep -v grep

# Check Redis consumer groups for unexpected consumers
docker compose exec redis redis-cli XINFO CONSUMERS "dalston:events:stream" orchestrators
```

**Choose one mode per session:**

- **Docker mode**: Use `make dev` exclusively. All services run in containers.
- **Local mode**: Run `docker compose stop` for services you're running locally.

**Why this matters**: Local processes persist across Claude Code sessions. A "zombie" orchestrator from a previous session can steal Redis events from the Docker orchestrator, causing jobs to hang indefinitely.

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
- `dalston/common/` - Shared types, events, audit, constants
- `dalston/db/` - SQLAlchemy ORM models, migrations, session management
- `engines/` - Engine implementations organized by stage
- `cli/` - Dalston CLI (`dalston_cli` package)
- `sdk/` - Python client SDK (`dalston_sdk` package)
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

# Redis connection (standard SDK format - not prefixed)
REDIS_URL=redis://localhost:6379

# Dalston-specific settings (all prefixed with DALSTON_)
DALSTON_S3_BUCKET=dalston-artifacts
DALSTON_S3_REGION=eu-west-2
DALSTON_LOG_LEVEL=INFO
DALSTON_LOG_FORMAT=json
DALSTON_METRICS_ENABLED=true
DALSTON_MAX_SESSIONS=4
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

## Code Standards

Full guidelines in `docs/CODE_STANDARDS.md`. Key non-obvious rules:

- Never call blocking I/O in async functions — use `asyncio.to_thread()`
- NO CASCADE DELETE — deletions explicit in application code
- Migrations append-only in production
- Handlers are glue only — all logic in the service layer
- `structlog` with correlation IDs everywhere
- All config via Pydantic `BaseSettings`, validated at startup
- Pagination on all list endpoints (cursor-based for mutable data, offset-based for reference data)
- N+1 queries are bugs — use eager loading or batch fetching

Mechanical style enforced by `ruff` and `mypy` — run `make lint` before committing.

## Shell & Permissions

When running commands that require environment variables (API keys, feature
flags, mode overrides), always set them via `export` in a prior step rather
than inline prefixes. For example:

```bash
# Preferred
export DALSTON_API_KEY=...
dalston transcribe ...

# Avoid: DALSTON_API_KEY=... dalston transcribe ...
```

This keeps the permissions allow-list clean. Never request approval for
env-var-prefixed command variants — use a standalone export instead.
