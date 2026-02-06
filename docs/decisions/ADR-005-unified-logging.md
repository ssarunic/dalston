# ADR-005: Unified Logging and Observability

## Status

Proposed

## Context

Dalston is a distributed system with multiple services (Gateway, Orchestrator, Session Router) and containerized engines communicating via Redis queues, pub/sub, and WebSockets. Debugging failures across these boundaries requires correlated, structured logs.

The current logging implementation is fragmented:

| Component | Library | Format | Context |
| --- | --- | --- | --- |
| Orchestrator | `structlog` | JSON with context binding | `job_id`, `task_id`, `engine_id` |
| Gateway | `logging.basicConfig()` | Plain text | None |
| Session Router | `logging.basicConfig()` | Plain text | None |
| Engine SDK | `logging.basicConfig()` | Plain text | Task ID in message string |
| Realtime SDK | `logging.basicConfig()` | Plain text | None |
| Engines (7 total) | `logging` | Plain text | Varies per engine |

This creates several problems:

1. **No correlation across services.** When a job fails in an engine, there is no shared identifier linking it back to the gateway request that created it. Debugging requires manually matching timestamps across plain-text logs from different containers.
2. **Inconsistent format.** The orchestrator emits machine-parseable JSON; all other services emit human-readable text. Log aggregators (ELK, Loki, CloudWatch) cannot uniformly index and query across components.
3. **No configurable log levels.** All services hardcode `logging.INFO`. Enabling debug logging for a single component requires a code change and redeployment.
4. **No distributed tracing.** There is no way to visualize the lifecycle of a job as a trace spanning gateway, orchestrator, and engines. Latency analysis requires manual log correlation.
5. **No operational metrics.** Queue depths, processing times, error rates, and worker utilization are only observable by querying Redis directly or reading logs.

The orchestrator's existing `structlog` setup demonstrates the desired pattern (JSON output, context binding via `logger.bind()`, ISO timestamps). The task is to extend this pattern to the entire system and layer tracing and metrics on top.

## Options Considered

### 1. Standardize on Python `logging` with JSON Formatter

Configure the standard library `logging` module with `python-json-logger` across all services.

**Pros:**

- No new dependencies beyond `python-json-logger`
- Familiar API for all Python developers
- Works with existing `logging.getLogger()` calls in engines

**Cons:**

- Context binding requires manual `extra={}` on every log call, which is verbose and error-prone
- No built-in context variable merging (must manually thread correlation IDs)
- Processor pipeline pattern (adding fields, filtering, formatting) requires custom code
- Orchestrator would need to be rewritten away from structlog

### 2. Standardize on `loguru`

Replace all logging with the `loguru` library.

**Pros:**

- Simple API (`from loguru import logger`)
- Built-in JSON serialization, rotation, colorization
- Context binding via `logger.bind()`

**Cons:**

- Incompatible with standard library `logging` — third-party libraries' log output requires an intercept handler
- Not designed for structured processor pipelines
- Less ecosystem support for OpenTelemetry integration
- Would require rewriting all 220+ existing logging calls and the orchestrator's structlog setup

### 3. Standardize on `structlog` (Chosen)

Extend the orchestrator's existing `structlog` configuration to all services via a shared module.

**Pros:**

- Already in use by the orchestrator (proven pattern in the codebase)
- Native context variable merging via `contextvars` (ideal for async FastAPI)
- Processor pipeline: add fields, filter, format in composable steps
- Wraps standard library `logging` — third-party libraries (uvicorn, FastAPI, boto3) automatically emit structured JSON
- First-class OpenTelemetry integration via processors
- `logger.bind()` for per-request context without modifying every log call

**Cons:**

- Learning curve for developers unfamiliar with structlog's processor model
- Requires migrating ~180 `logging.*()` calls outside the orchestrator
- Slightly more complex configuration than `logging.basicConfig()`

## Decision

Adopt `structlog` as the unified logging library across all Dalston services. Implement observability in three milestones:

| Milestone | Scope |
| --- | --- |
| [M18](../plan/milestones/M18-unified-structured-logging.md) | Shared logging module, correlation IDs, structlog migration |
| [M19](../plan/milestones/M19-distributed-tracing.md) | OpenTelemetry instrumentation and trace export |
| [M20](../plan/milestones/M20-metrics-dashboards.md) | Prometheus metrics and Grafana dashboards |

### Key Design Rules

1. **Single configuration point.** All services call `dalston.logging.configure()` at startup. No per-service `logging.basicConfig()` or `structlog.configure()`.
2. **Correlation IDs at the boundary.** The gateway generates a `request_id` for every HTTP request and WebSocket connection. This ID propagates through Redis task metadata to engines.
3. **JSON in production, human-readable in development.** The `LOG_FORMAT` environment variable switches between `json` and `console` output.
4. **Log level via environment.** The `LOG_LEVEL` environment variable controls verbosity per service without code changes.
5. **Engines inherit context.** The engine SDK extracts `request_id`, `job_id`, and `task_id` from task metadata and binds them automatically.

### Correlation ID Flow

```
Client Request
    │
    ▼
┌─────────┐  request_id = uuid4()
│ Gateway  │──────────────────────────────────────────┐
└────┬─────┘                                          │
     │ Redis pub/sub: job_created                     │
     │ (request_id in job metadata)                   │
     ▼                                                │
┌──────────────┐  logger.bind(request_id, job_id)     │
│ Orchestrator │──────────────────────────────────┐   │
└────┬─────────┘                                  │   │
     │ Redis queue: task payload                  │   │
     │ (request_id, job_id, task_id in metadata)  │   │
     ▼                                            │   │
┌─────────┐  logger.bind(request_id, job_id,      │   │
│ Engine  │  task_id) — automatic via SDK          │   │
└─────────┘                                       │   │
                                                  │   │
All logs share request_id ◄───────────────────────┘───┘
```

## Consequences

### Easier

- **Cross-service debugging.** Filter all logs for a single request: `jq 'select(.request_id == "abc123")'`
- **Log aggregation.** Uniform JSON format works with ELK, Loki, CloudWatch without per-service parsing rules
- **Production debugging.** Change `LOG_LEVEL=DEBUG` on a single service without redeployment (restart only)
- **Onboarding.** One logging pattern to learn, documented in a single module
- **Future tracing.** Structured logs with correlation IDs are the foundation for OpenTelemetry spans (M19)

### Harder

- **Migration effort.** ~180 logging calls across gateway, session router, engines, and SDKs must be reviewed
- **Engine developer experience.** Engine authors must use `structlog` instead of `print()` or `logging.info()` — requires updating engine SDK documentation
- **Local development.** JSON logs are harder to scan visually (mitigated by `LOG_FORMAT=console` for development)

### Mitigations

- Migration is mechanical (search-and-replace `logging.getLogger` → `structlog.get_logger`, update call signatures) and can be done incrementally per component
- Engine SDK handles context binding automatically — engine authors call `self.logger.info()` as before, with correlation IDs injected by the SDK
- Console formatter provides colored, human-readable output identical to current format for local development
