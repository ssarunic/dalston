# M19: Distributed Tracing

| | |
|---|---|
| **Goal** | Visualize the full lifecycle of a job as a trace spanning all services |
| **Duration** | 3-4 days |
| **Dependencies** | M18 complete (correlation IDs and structured logging) |
| **Deliverable** | View a job's waterfall trace in Jaeger showing gateway → orchestrator → engine spans |
| **Status** | Not Started |

## User Story

> *"As an operator, I can open a trace viewer and see exactly how long each stage of a transcription job took, which service processed it, and where bottlenecks occur."*

---

## Steps

### 19.1: OpenTelemetry Core Setup

```text
dalston/telemetry.py
```

**Deliverables:**

- `configure_tracing(service_name: str)` function that initializes the OpenTelemetry SDK
- OTLP exporter for trace data (supports Jaeger, Tempo, Datadog, and any OTLP-compatible backend)
- `OTEL_ENABLED` environment variable to enable/disable tracing (default: `false`)
- `OTEL_EXPORTER_OTLP_ENDPOINT` environment variable for exporter target
- No-op tracer when disabled (zero performance overhead)
- Trace context linked to structlog correlation IDs (same `request_id` appears in logs and traces)

**Dependencies Added:**

```
opentelemetry-api>=1.20.0
opentelemetry-sdk>=1.20.0
opentelemetry-exporter-otlp-proto-grpc>=1.20.0
opentelemetry-instrumentation-fastapi>=0.41b0
opentelemetry-instrumentation-redis>=0.41b0
opentelemetry-instrumentation-httpx>=0.41b0
```

---

### 19.2: Gateway Instrumentation

**Files Modified:**

- `dalston/gateway/main.py` — Initialize tracing, add FastAPI auto-instrumentation

**Deliverables:**

- FastAPI auto-instrumentation creates spans for every HTTP request
- Span attributes include: `http.method`, `http.route`, `http.status_code`, `dalston.request_id`
- WebSocket connections create a parent span for the session lifetime
- Job creation creates a child span: `gateway.create_job`
- `traceparent` header propagated on outgoing HTTP calls (if any)
- Trace ID linked to `request_id` in structlog context for log-trace correlation

**Span Hierarchy (Batch Request):**

```
[gateway] POST /v1/audio/transcriptions          ← auto-instrumented
  ├── [gateway] upload_to_s3                      ← manual span
  ├── [gateway] create_job                        ← manual span
  └── [gateway] publish_job_created_event         ← manual span
```

---

### 19.3: Orchestrator Instrumentation

**Files Modified:**

- `dalston/orchestrator/main.py` — Initialize tracing
- `dalston/orchestrator/handlers.py` — Create spans for job handling and task scheduling

**Deliverables:**

- Trace context extracted from job metadata (propagated from gateway)
- Job handling creates a span: `orchestrator.handle_job`
- Task scheduling creates child spans: `orchestrator.schedule_task`
- DAG building creates a span: `orchestrator.build_dag`
- Span attributes include: `dalston.job_id`, `dalston.task_id`, `dalston.engine_id`
- Trace context serialized into task payload metadata for engine propagation

**Span Hierarchy (Job Processing):**

```
[orchestrator] handle_job_created                 ← linked to gateway trace
  ├── [orchestrator] build_dag                    ← child span
  ├── [orchestrator] schedule_task (prepare)      ← child span
  │     ... (engine processing, linked via task metadata) ...
  ├── [orchestrator] handle_task_completed        ← new span, linked to same trace
  ├── [orchestrator] schedule_task (transcribe)   ← child span
  │     ...
  └── [orchestrator] mark_job_completed           ← child span
```

---

### 19.4: Engine SDK Instrumentation

**Files Modified:**

- `dalston/engine_sdk/runner.py` — Extract trace context from task metadata, create processing spans
- `dalston/engine_sdk/base.py` — Wrap `process()` in a span

**Deliverables:**

- `EngineRunner` extracts `traceparent` from task metadata and creates a linked span
- Each `engine.process()` call is wrapped in a span: `engine.{engine_id}.process`
- Span attributes: `dalston.task_id`, `dalston.job_id`, `dalston.engine_id`, `dalston.stage`
- S3 download/upload operations create child spans via auto-instrumentation
- Redis operations auto-instrumented
- Engine authors do not need to add any tracing code

**Span Hierarchy (Engine Processing):**

```
[engine] engine.faster-whisper.process            ← linked to orchestrator trace
  ├── [engine] download_input (S3)                ← auto-instrumented
  ├── [engine] transcribe                         ← engine processing time
  └── [engine] upload_output (S3)                 ← auto-instrumented
```

---

### 19.5: Session Router and Realtime Instrumentation

**Files Modified:**

- `dalston/session_router/router.py` — Create spans for session allocation
- `dalston/session_router/allocator.py` — Span for worker selection
- `dalston/realtime_sdk/base.py` — Span per WebSocket session
- `dalston/realtime_sdk/session.py` — Spans for audio processing and transcript generation

**Deliverables:**

- Session allocation creates a span: `session_router.allocate`
- Real-time sessions create a long-running span for the session lifetime
- Audio chunk processing creates child spans (sampled to avoid overhead)
- Transcript events (partial, final) create spans with transcript metadata

**Span Hierarchy (Real-time Session):**

```
[gateway] WebSocket /v1/audio/transcriptions/stream
  └── [session_router] allocate_session
        └── [realtime] session (long-running)
              ├── [realtime] process_audio_chunk    ← sampled
              ├── [realtime] vad_endpoint
              ├── [realtime] generate_transcript
              └── [realtime] session_close
```

---

### 19.6: Jaeger Service for Development

**Files Modified:**

- `docker-compose.yml` — Add Jaeger all-in-one service
- `.env.example` — Document tracing environment variables

**Deliverables:**

- Jaeger all-in-one container in Docker Compose (UI on port 16686, OTLP on port 4317)
- Disabled by default via Docker Compose profile (`--profile tracing`)
- All services configured to export to Jaeger when `OTEL_ENABLED=true`
- Documentation for accessing the Jaeger UI

**Docker Compose Addition:**

```yaml
jaeger:
  image: jaegertracing/all-in-one:1.54
  ports:
    - "16686:16686"   # Jaeger UI
    - "4317:4317"     # OTLP gRPC
  profiles:
    - tracing
```

**Environment Variables:**

```bash
# Tracing configuration
OTEL_ENABLED=false                              # Enable OpenTelemetry tracing
OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4317  # OTLP exporter target
OTEL_SERVICE_NAME=dalston-gateway               # Set automatically by configure_tracing()
```

---

### 19.7: Log-Trace Correlation

**Files Modified:**

- `dalston/logging.py` — Add structlog processor that injects `trace_id` and `span_id` from active OpenTelemetry span
- `dalston/telemetry.py` — Ensure `request_id` from structlog contextvars is set as span attribute

**Deliverables:**

- Every structured log line includes `trace_id` and `span_id` when tracing is enabled
- Jaeger can link to log aggregator queries filtered by trace ID
- Log aggregators can link to Jaeger traces via trace ID
- When tracing is disabled, `trace_id` and `span_id` are omitted (no noise)

**Example Log with Trace Correlation:**

```json
{
  "timestamp": "2026-02-05T14:30:00.123Z",
  "level": "info",
  "service": "gateway",
  "request_id": "req_a1b2c3d4",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_id": "00f067aa0ba902b7",
  "event": "job_created",
  "job_id": "job_xyz789"
}
```

---

## Verification

```bash
# Start services with tracing enabled
docker compose --profile tracing up -d
OTEL_ENABLED=true docker compose up -d

# Submit a job
JOB_ID=$(curl -s -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@test.wav" | jq -r '.id')

# Wait for completion
sleep 10

# Open Jaeger UI
# → http://localhost:16686

# Search for traces:
# Service: dalston-gateway
# Operation: POST /v1/audio/transcriptions
# → Click trace to see full waterfall: gateway → orchestrator → engines

# Verify log-trace correlation
docker compose logs | jq 'select(.job_id == "'$JOB_ID'") | .trace_id' | head -1
# → Use this trace_id in Jaeger to find the corresponding trace
```

---

## Checkpoint

- [ ] **OpenTelemetry SDK** initialized in all services via `dalston.telemetry.configure_tracing()`
- [ ] **Gateway** spans cover HTTP requests, job creation, and S3 uploads
- [ ] **Orchestrator** spans cover job handling, DAG building, and task scheduling
- [ ] **Engines** spans cover task processing, automatically linked to parent trace
- [ ] **Jaeger** shows end-to-end waterfall for a complete job
- [ ] **Log-trace correlation** links structured logs to traces via `trace_id`
- [ ] **Tracing disabled by default** — zero overhead when `OTEL_ENABLED=false`
- [ ] **No code changes** required in engine `process()` methods

**Next**: [M20: Metrics & Dashboards](M20-metrics-dashboards.md) — Prometheus metrics and Grafana dashboards
