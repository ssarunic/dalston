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
WORKER_ID=dev-worker REDIS_URL=redis://localhost:6379 python engine.py
```

### Local Docker Setup

**Important**: Local Docker does not use volume mounts - files are baked into the images. You must rebuild containers after any code change before testing:

```bash
# Rebuild specific service after code changes
docker compose build <service-name>
docker compose up -d <service-name>

# Or rebuild and start in one command
docker compose up -d --build <service-name>
```

### Docker Compose Operations

```bash
# Start all services
docker compose up -d

# Start core services only (minimal setup with word timestamps)
docker compose up -d gateway orchestrator redis postgres minio minio-init \
  stt-batch-prepare stt-batch-transcribe-whisper-cpu stt-batch-align-whisperx-cpu stt-batch-merge

# Start without word-level alignment (faster, smaller setup)
# Note: Submit jobs with timestamps_granularity=segment to skip alignment
docker compose up -d gateway orchestrator redis postgres minio minio-init \
  stt-batch-prepare stt-batch-transcribe-whisper-cpu stt-batch-merge

# Start with real-time workers
docker compose up -d gateway orchestrator redis \
  stt-batch-transcribe-whisper-cpu stt-batch-merge \
  stt-rt-transcribe-whisper-1 stt-rt-transcribe-whisper-2

# Scale engines for high load
docker compose up -d --scale stt-batch-transcribe-whisper-cpu=2 --scale stt-batch-diarize-pyannote-v31-cpu=2

# View logs
docker compose logs -f gateway
docker compose logs -f stt-batch-transcribe-whisper-cpu

# Stop services
docker compose down

# Rebuild specific service
docker compose build stt-batch-transcribe-whisper-cpu
docker compose up -d --build stt-batch-transcribe-whisper-cpu
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
