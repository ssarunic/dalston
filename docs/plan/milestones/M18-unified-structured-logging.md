# M18: Unified Structured Logging

| | |
|---|---|
| **Goal** | All services emit structured JSON logs with correlation IDs |
| **Duration** | 3-4 days |
| **Dependencies** | None (can start immediately, applies to all existing components) |
| **Deliverable** | Filter logs for a single request across gateway, orchestrator, and engines |
| **Status** | Complete |

## User Story

> *"As an operator, I can trace a transcription request from the gateway through the orchestrator and into engines using a single `request_id`, with all logs in a consistent JSON format."*

---

## Steps

### 18.1: Shared Logging Module

```text
dalston/logging.py
```

**Deliverables:**

- `configure(service_name: str)` function that sets up structlog for any Dalston service
- Processor pipeline: `contextvars` merge, add log level, ISO timestamp, service name, JSON renderer
- `LOG_LEVEL` environment variable support (default: `INFO`)
- `LOG_FORMAT` environment variable: `json` (default) for production, `console` for development
- Console renderer with colors for local development
- Standard library integration so third-party libraries (uvicorn, boto3, aiohttp) emit structured JSON
- Unit tests for both formatters and log level configuration

**Processor Pipeline:**

```
contextvars.merge_contextvars     ← injects request_id, job_id from async context
add_log_level                     ← "info", "warning", "error"
TimeStamper(fmt="iso")            ← "2026-02-05T14:30:00.000Z"
add_service_name                  ← custom processor: {"service": "gateway"}
JSONRenderer() | ConsoleRenderer  ← based on LOG_FORMAT
```

**Example Output (JSON):**

```json
{
  "timestamp": "2026-02-05T14:30:00.123Z",
  "level": "info",
  "service": "gateway",
  "request_id": "req_a1b2c3d4",
  "event": "job_created",
  "job_id": "job_xyz789",
  "file_name": "podcast.mp3",
  "duration_seconds": 342.5
}
```

**Example Output (Console):**

```
2026-02-05T14:30:00.123Z [info     ] job_created                    [gateway] request_id=req_a1b2c3d4 job_id=job_xyz789
```

---

### 18.2: Correlation ID Middleware

```text
dalston/gateway/middleware/correlation.py
```

**Deliverables:**

- ASGI middleware that generates a `request_id` (UUID4 with `req_` prefix) for every incoming HTTP request
- Reads `X-Request-ID` header if provided by client (allows external correlation)
- Stores `request_id` in `structlog.contextvars` so all downstream log calls include it automatically
- Sets `X-Request-ID` response header for client-side correlation
- WebSocket connection handler sets `request_id` at connection start, persists for session lifetime
- Unit tests verifying header propagation and contextvars binding

---

### 18.3: Propagate Correlation IDs Through Task Metadata

**Files Modified:**

- `dalston/gateway/services/jobs.py` — Include `request_id` in job creation metadata
- `dalston/orchestrator/handlers.py` — Read `request_id` from job metadata, bind to logger, pass to task payloads
- `dalston/engine_sdk/runner.py` — Extract `request_id`, `job_id`, `task_id` from task payload, bind to logger before calling `engine.process()`

**Deliverables:**

- Job metadata schema includes optional `request_id` field
- Orchestrator binds `request_id` from job metadata when processing events
- Engine SDK `EngineRunner` automatically binds `request_id`, `job_id`, `task_id` before each task
- Engine authors get correlation context without any changes to their `process()` method
- Integration test: submit job via gateway, verify `request_id` appears in engine logs

**Task Payload Extension:**

```json
{
  "task_id": "task_001",
  "job_id": "job_xyz789",
  "engine_id": "faster-whisper",
  "metadata": {
    "request_id": "req_a1b2c3d4"
  }
}
```

---

### 18.4: Migrate Gateway to Shared Logging

**Files Modified:**

- `dalston/gateway/main.py` — Replace `logging.basicConfig()` with `dalston.logging.configure("gateway")`
- `dalston/gateway/middleware/error_handler.py` — Switch to `structlog.get_logger()`
- `dalston/gateway/middleware/auth.py` — Switch to `structlog.get_logger()`
- `dalston/gateway/api/v1/realtime.py` — Switch to `structlog.get_logger()`
- `dalston/gateway/services/webhook.py` — Already uses structlog, verify compatible with shared config

**Deliverables:**

- All gateway log calls use `structlog.get_logger()`
- Error handler includes `request_id` and `status_code` in error logs
- No `logging.basicConfig()` calls remain in gateway
- Existing tests pass without modification

---

### 18.5: Migrate Session Router to Shared Logging

**Files Modified:**

- `dalston/session_router/router.py` — Switch to `structlog.get_logger()`
- `dalston/session_router/allocator.py` — Switch to `structlog.get_logger()`
- `dalston/session_router/registry.py` — Switch to `structlog.get_logger()`
- `dalston/session_router/health.py` — Switch to `structlog.get_logger()`

**Deliverables:**

- All session router log calls use `structlog.get_logger()` with context binding
- Session allocation logs include `session_id` and `worker_id`
- Health check logs include `worker_id` and health status

---

### 18.6: Migrate Engine SDK and Realtime SDK

**Files Modified:**

- `dalston/engine_sdk/runner.py` — Replace `logging.basicConfig()` with `dalston.logging.configure("engine-{engine_id}")`
- `dalston/engine_sdk/base.py` — Provide `self.logger` as a bound structlog logger on the `Engine` base class
- `dalston/realtime_sdk/base.py` — Switch to `structlog.get_logger()`
- `dalston/realtime_sdk/session.py` — Switch to `structlog.get_logger()` with `session_id` binding
- `dalston/realtime_sdk/vad.py` — Switch to `structlog.get_logger()`

**Deliverables:**

- Engine base class provides `self.logger` pre-bound with `engine_id`
- EngineRunner binds `task_id`, `job_id`, `request_id` per task (from step 18.3)
- Realtime SDK binds `session_id`, `worker_id` per session
- Engine authors continue calling `self.logger.info(...)` — no API change for existing engines

---

### 18.7: Migrate Engines

**Files Modified:**

All 7 engine implementations:

- `engines/prepare/audio-prepare/engine.py`
- `engines/transcribe/faster-whisper/engine.py`
- `engines/align/whisperx-align/engine.py`
- `engines/diarize/pyannote-3.1/engine.py`
- `engines/diarize/pyannote-4.0/engine.py`
- `engines/merge/final-merger/engine.py`
- `engines/realtime/whisper-streaming/engine.py`

**Deliverables:**

- Replace `logging.getLogger(__name__)` with `structlog.get_logger()` or use `self.logger` from base class
- Remove any per-engine `logging.basicConfig()` calls
- Context (task_id, job_id, request_id) injected by SDK — no manual binding needed in engines
- Verify each engine logs correctly in both JSON and console modes

---

### 18.8: Migrate Orchestrator to Shared Config

**Files Modified:**

- `dalston/orchestrator/main.py` — Replace inline `structlog.configure(...)` with `dalston.logging.configure("orchestrator")`
- `dalston/orchestrator/handlers.py` — No changes needed (already uses `structlog.get_logger()`)
- `dalston/orchestrator/scheduler.py` — No changes needed (already uses `structlog.get_logger()`)

**Deliverables:**

- Orchestrator uses shared config instead of its own inline `structlog.configure()`
- Existing context binding (`job_id`, `task_id`, `engine_id`) preserved
- `request_id` propagation from job metadata added (from step 18.3)

---

### 18.9: Update Docker Compose and Environment

**Files Modified:**

- `docker-compose.yml` — Add `LOG_LEVEL` and `LOG_FORMAT` environment variables to all services
- `.env.example` — Document new logging environment variables

**Deliverables:**

- All services default to `LOG_LEVEL=INFO`, `LOG_FORMAT=json`
- Development override: `LOG_FORMAT=console` for human-readable output
- Per-service log level override possible (e.g., `LOG_LEVEL=DEBUG` only on orchestrator)

**Environment Variables:**

```bash
# Logging configuration
LOG_LEVEL=INFO          # DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_FORMAT=json         # json (production) or console (development)
```

---

## Verification

```bash
# Start services with console logging for readability
LOG_FORMAT=console docker compose up -d

# Submit a job
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@test.wav" \
  -v 2>&1 | grep X-Request-ID
# → X-Request-ID: req_a1b2c3d4

# Filter all logs for this request
docker compose logs | grep req_a1b2c3d4
# → gateway:       ... request_id=req_a1b2c3d4 event=job_created ...
# → orchestrator:  ... request_id=req_a1b2c3d4 event=task_scheduled ...
# → engine-...:    ... request_id=req_a1b2c3d4 event=task_started ...
# → engine-...:    ... request_id=req_a1b2c3d4 event=task_completed ...

# Switch to JSON for machine parsing
LOG_FORMAT=json docker compose up -d
docker compose logs --no-log-prefix | jq 'select(.request_id == "req_a1b2c3d4")'

# Verify configurable log level
LOG_LEVEL=DEBUG docker compose up -d orchestrator
docker compose logs orchestrator | grep '"level":"debug"'
```

---

## Checkpoint

- [x] **Shared module** `dalston.logging.configure()` works with both JSON and console output
- [x] **Correlation ID** middleware generates and propagates `request_id`
- [x] **Gateway** emits structured JSON with `request_id` on every log line
- [x] **Orchestrator** uses shared config, includes `request_id` from job metadata
- [x] **Engines** automatically include `request_id`, `job_id`, `task_id` via SDK
- [x] **Session Router** emits structured JSON with `session_id`, `worker_id`
- [x] **LOG_LEVEL** and **LOG_FORMAT** environment variables work across all services
- [x] **No** `logging.basicConfig()` calls remain anywhere in the codebase

**Next**: [M19: Distributed Tracing](M19-distributed-tracing.md) — OpenTelemetry instrumentation
