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
DALSTON_WORKER_ID=dev-worker REDIS_URL=redis://localhost:6379 python engine.py
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

These guidelines cover design decisions requiring judgment. Mechanical style rules
are enforced by `ruff` and `mypy` — run `make lint` before committing.

### Architecture

- Apply SOLID principles. Each module/class has one reason to change.
- Use dependency injection — never instantiate external dependencies inside business logic.
- All external integrations (Redis, PostgreSQL, model containers) go through
  protocol/ABC abstractions so they can be swapped or mocked.
- Prefer composition. No inheritance deeper than 2 levels.
- Functions should do one thing well. If you need to scroll to read it, split it.
- Files represent one cohesive concept. Split when you have unrelated concepts.

### Async Code

- Never call blocking I/O in async functions. Use `asyncio.to_thread()` for unavoidable sync calls.
- Prefer `async with` for connections/sessions to ensure cleanup.
- Use `asyncio.TaskGroup` (3.11+) or `asyncio.gather` with `return_exceptions=True`.
- Timeouts on all external calls: `async with asyncio.timeout(seconds)`.
- No fire-and-forget tasks without error handling — use background task managers.

### Database

- Indices on all columns used in WHERE, JOIN, or ORDER BY. Composite indices for common query patterns.
- Foreign keys for referential integrity, but NO CASCADE DELETE — deletions must be explicit in application code.
- Always paginate list endpoints. Default page size: 25, max: 100.
  - Cursor-based: jobs, tasks, realtime sessions, webhook deliveries (data changes between requests)
  - Offset-based: models, workers, webhook endpoints (rarely changing reference data)
  - Always return `has_more` and the appropriate cursor/offset in response metadata.
- Bulk operations in batches (500-1000 rows) to avoid lock contention and memory spikes.
- Timestamps: `created_at` (immutable), `updated_at` (auto-touch). Use UTC everywhere.
- Soft deletes (`deleted_at`) for audit trails on business-critical entities.
- Migrations are append-only in production. Never modify a released migration.
- N+1 queries are bugs. Use eager loading or batch fetching.

### API Contracts

- All mutations must be idempotent — retrying a request produces the same result.
- Use `X-Request-ID` header for tracing; echo it in responses.

### HTTP Handlers

- Handlers are glue code only: parse request, call service, format response.
- No business logic in handlers — all logic lives in the service layer.
- Validation via Pydantic models, not manual checks in handlers.
- Handlers catch service exceptions and map to appropriate HTTP status codes.
- Keep handlers small enough to see the full request→response flow at a glance.

### Error Handling

- Domain-specific exceptions inheriting from a project base exception.
- Always include context in error messages: what failed, with what input, why.
- Preserve exception chains — the original cause matters for debugging.
- Fail fast on invalid state. Don't silently continue with bad data.

### Logging

- Structured logging with `structlog` — always include correlation IDs.
- Log levels: DEBUG for developer detail, INFO for business events, WARNING for recoverable issues, ERROR for failures needing attention.
- Include context: `log.info("job_completed", job_id=job.id, duration_ms=elapsed, segments=len(result))`
- Never log secrets, tokens, or full audio data.
- Trace IDs must propagate through Gateway → Orchestrator → Engines.

### Configuration

- All config via Pydantic `BaseSettings` with environment variable sources.
- Secrets never in code or defaults — fail fast if missing in production.
- Feature flags for gradual rollouts of new engines/behaviors.
- Validate config at startup, not at first use.

### Concurrency

- Redis operations that must be atomic: use Lua scripts or transactions.
- Assume any operation can fail mid-flight — design for resumability.
- No in-memory state that can't be reconstructed from Redis/DB.
- Distributed locks with TTL and automatic renewal for long operations.
- Queue consumers must be idempotent — messages may be delivered more than once.

### Security

- Validate and sanitize all file uploads — never trust filenames or MIME types.
- API keys hashed, never stored plaintext.
- Rate limiting at gateway level, per-client and per-endpoint.
- No shell commands with user-provided input. If unavoidable, use `shlex.quote()`.

### Naming

- Functions: verb_noun (`process_audio`, `validate_request`)
- Classes: noun (`TranscriptionJob`, `PipelineOrchestrator`)
- Booleans: `is_`, `has_`, `can_` prefix
- Domain abbreviations are fine when standard (id, url, db, vad, asr, stt)
- No other abbreviations — clarity over brevity.

### Testing

- Every public function gets a test. Test behavior, not implementation details.
- Use Arrange-Act-Assert structure with blank line separators.
- Fixtures for shared setup, factories for test data.
- No test should depend on another test's state.
- New endpoints require contract tests.

### Code Review Checklist

- Flag any function doing more than one thing.
- Flag duplicated logic appearing 3+ times.
- Flag error handling that swallows context.
- Flag magic strings or numbers — extract to named constants.
- Flag async functions calling blocking code without `to_thread()`.
- Flag missing correlation ID propagation.
- Flag mutable shared state without synchronization.
- Flag N+1 query patterns.
- Flag missing pagination on list endpoints.
- Suggest simpler alternatives if cyclomatic complexity is high.
