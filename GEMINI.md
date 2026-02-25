# Gemini CLI Project Context: Dalston

Dalston is a modular, self-hosted audio transcription server featuring an ElevenLabs-compatible API. It supports both high-throughput batch processing and low-latency real-time streaming.

## Project Overview

- **Core Technology:** Python 3.11+, FastAPI, SQLAlchemy (Async), Redis, PostgreSQL.
- **Frontend:** React (TypeScript) with Vite and Tailwind CSS.
- **Infrastructure:** Containerized architecture (Docker Compose), AWS/S3 compatible storage, and Terraform for infra-as-code.
- **Architecture:**
  - **Gateway:** REST and WebSocket entry points.
  - **Orchestrator:** Manages batch job DAGs and Redis-based task queues.
  - **Session Router:** Manages real-time worker allocation.
  - **Engines:** Modular processors for transcription (Whisper, Parakeet), alignment (WhisperX), diarization (Pyannote), and PII detection.

## Building and Running

The project uses a `Makefile` to orchestrate development tasks.

### Local Development

- `make dev`: Start full local stack (PostgreSQL, Redis, MinIO, Gateway, Orchestrator, CPU engines).
- `make dev-minimal`: Start minimal stack (infra + gateway + faster-whisper only).
- `make dev-gpu`: Start with GPU-accelerated engines (requires NVIDIA GPU).
- `make dev-observability`: Start with Jaeger, Prometheus, and Grafana.
- `make stop`: Stop all services.

### Testing & Quality

- `make test`: Run `pytest` suite.
- `make lint`: Run `ruff` and `mypy`.
- `make fmt`: Format code using `ruff`.
- `make validate`: Check Docker Compose configurations.

### Database Migrations

- Uses Alembic. Run migrations via `alembic upgrade head` (usually handled by container startup or manual intervention during development).

## Key Directory Structure

- `/dalston`: Core application logic.
  - `/gateway`: API handlers (REST/WS).
  - `/orchestrator`: Batch job scheduling.
  - `/engine_sdk`: Base SDK for batch engines.
  - `/realtime_sdk`: Base SDK for real-time engines.
- `/engines`: Specialized engine implementations (stt-transcribe, stt-align, stt-diarize, etc.).
- `/web`: React-based management console.
- `/cli`: Python CLI tool for interacting with the server.
- `/sdk`: Python client SDK.
- `/docs`: Comprehensive documentation, including architecture and API specs.

## Development Conventions

- **Detailed Guidelines:** See `CLAUDE.md` for exhaustive engineering standards, async patterns, and database best practices.
- **Code Style:** Enforced by `ruff`. Line length is 88.
- **Configuration:** Managed via Pydantic `BaseSettings`. Key variables: `DATABASE_URL`, `REDIS_URL`, `S3_ENDPOINT_URL`, `HF_TOKEN`.
- **Async:** Strictly non-blocking. Use `asyncio.to_thread()` for sync I/O.
- **Testing:** New features MUST include tests. Use Arrange-Act-Assert.

## API Compatibility

- **Native:** `/v1/audio/transcriptions/*`
- **ElevenLabs:** `/v1/speech-to-text/*`
- **Real-time:** `/v1/audio/transcriptions/stream` (Native) or `/v1/speech-to-text/realtime` (ElevenLabs).
