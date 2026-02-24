# Observability Workflow Guide

How to use Dalston's observability stack (M18–M20) day-to-day for debugging, performance analysis, and operational monitoring.

**Prerequisites**: Services running with `--profile monitoring` and/or `--profile tracing` enabled.

```bash
# Start with full observability
OTEL_ENABLED=true docker compose --profile monitoring --profile tracing up -d

# Jaeger UI:     http://localhost:16686
# Grafana:       http://localhost:3001  (admin / dalston)
# Prometheus:    http://localhost:9090
```

---

## Core debugging workflow

Start with the trace, then follow the logs. This replaces hunting through `docker logs` across five containers.

### 1. Find the trace

When a job behaves unexpectedly — slow, failed, wrong output — open Jaeger and search by the `dalston.job_id` attribute:

- **Service**: `dalston-gateway`
- **Tags**: `dalston.job_id=<job_id>`

The trace shows the full waterfall: gateway request → orchestrator DAG build → task scheduling → engine processing per stage. You immediately see which stage consumed the time or where the error originated.

The span hierarchy for a batch job looks like:

```
[gateway]       POST /v1/audio/transcriptions
  ├── upload_to_s3
  ├── create_job
  └── publish_job_created_event
[orchestrator]  handle_job_created           ← linked via Redis event trace context
  ├── build_dag
  ├── schedule_task (prepare)
  │   └── [engine] engine.audio-prepare.process
  │         ├── engine.download_input
  │         ├── engine.process
  │         └── engine.upload_output
  ├── schedule_task (transcribe)
  │   └── [engine] engine.faster-whisper.process
  │         ├── engine.download_input
  │         ├── engine.process
  │         └── engine.upload_output
  ...
  └── mark_job_completed
```

### 2. Correlate with logs

Every structured log line includes `trace_id` and `span_id` when tracing is enabled (see `dalston/logging.py:37`). Copy the `trace_id` from Jaeger, then filter logs:

```bash
# JSON mode
docker compose logs --no-log-prefix | jq 'select(.trace_id == "<trace_id>")'

# Or filter by request_id (propagated from gateway through all services)
docker compose logs --no-log-prefix | jq 'select(.request_id == "req_<id>")'
```

The `request_id` is set by the correlation middleware (`dalston/gateway/middleware/correlation.py`) and propagated through Redis task metadata to the orchestrator and engines. Every log line from every service for a single request shares the same `request_id`.

### 3. Check the stage breakdown via API

For user-facing debugging, the job status endpoint includes a `stages` array showing each pipeline stage's status, timing, and errors:

```bash
curl -s http://localhost:8000/v1/audio/transcriptions/<job_id> \
  -H "Authorization: Bearer $API_KEY" | jq '.stages'
```

Each stage entry includes `duration_ms`, `engine_id`, `status`, `retries`, and `error`. This is the programmatic equivalent of the Jaeger waterfall, useful when you don't have access to the tracing UI.

For deeper inspection, fetch the raw input/output artifacts for any stage:

```bash
curl -s http://localhost:8000/v1/audio/transcriptions/<job_id>/tasks/<task_id>/artifacts \
  -H "Authorization: Bearer $API_KEY" | jq '.output.data'
```

---

## Dashboards

The pre-built Grafana dashboard (`docker/grafana/dashboards/dalston-overview.json`) covers the basics: request rate, error rate, latency, queue depth, engine performance, and real-time sessions. Below are the additional focused dashboards worth building.

### Operations dashboard

The metrics you need for at-a-glance health:

| Panel | PromQL | What it tells you |
|-------|--------|-------------------|
| Engine processing time (p50/p95/p99) | `histogram_quantile(0.95, sum(rate(dalston_engine_task_duration_seconds_bucket[5m])) by (engine_id, le))` | Whether any engine is degrading |
| Queue depth by engine | `dalston_queue_depth` | Whether workers are keeping up |
| Error rate by engine | `sum(rate(dalston_engine_tasks_processed_total{status="failure"}[5m])) by (engine_id) / sum(rate(dalston_engine_tasks_processed_total[5m])) by (engine_id)` | Broken engines |
| Job completion rate | `sum(rate(dalston_orchestrator_jobs_total{status="completed"}[5m]))` | Throughput |
| Queue wait time (p95) | `histogram_quantile(0.95, sum(rate(dalston_engine_queue_wait_seconds_bucket[5m])) by (engine_id, le))` | Worker pool sizing |

### Cost dashboard (AWS hybrid setup)

Correlate job counts and audio durations against compute:

| Panel | Source | Purpose |
|-------|--------|---------|
| Jobs completed/hour | `dalston_orchestrator_jobs_total` | Throughput |
| Audio minutes processed | Derive from `job.audio_duration` in PostgreSQL (not yet exposed as a metric) | Volume |
| Engine processing time per audio minute | `dalston_engine_task_duration_seconds / audio_duration` | Efficiency ratio |

**Note**: Audio duration is tracked in PostgreSQL (`jobs.audio_duration` column) and included in the job status API response as `audio_duration_seconds`, but is **not currently a Prometheus metric or span attribute**. See [gaps to address](#gaps-to-address) below.

### Per-engine performance

The `engine_id` label on `dalston_engine_task_duration_seconds` implicitly encodes the model (e.g., `stt-batch-transcribe-faster-whisper-large-v3`). Use this to compare:

```promql
# Processing time by engine variant
histogram_quantile(0.95,
  sum(rate(dalston_engine_task_duration_seconds_bucket[5m])) by (engine_id, le)
)
```

Language is **not** currently a metric label. To analyze per-language performance, you need to query Jaeger by span attributes or run queries against PostgreSQL job metadata. See [gaps to address](#gaps-to-address).

---

## Alerting

Three alerts cover the critical failure modes:

### 1. Queue depth sustained above threshold

Workers are down or overwhelmed.

```yaml
# Prometheus alerting rule
- alert: QueueDepthHigh
  expr: dalston_queue_depth > 20
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "Engine {{ $labels.engine_id }} queue depth at {{ $value }}"
```

### 2. Engine error rate exceeds 5%

Container crash or model issue.

```yaml
- alert: EngineErrorRateHigh
  expr: |
    (
      sum(rate(dalston_engine_tasks_processed_total{status="failure"}[10m])) by (engine_id)
      /
      sum(rate(dalston_engine_tasks_processed_total[10m])) by (engine_id)
    ) > 0.05
  for: 5m
  labels:
    severity: critical
  annotations:
    summary: "Engine {{ $labels.engine_id }} error rate at {{ $value | humanizePercentage }}"
```

### 3. No jobs processed during expected hours

Silent failure in worker or orchestrator.

```yaml
- alert: NoJobsProcessed
  expr: |
    sum(increase(dalston_orchestrator_jobs_total[30m])) == 0
    and ON() (hour() >= 8 and hour() <= 18)
  for: 30m
  labels:
    severity: warning
  annotations:
    summary: "No jobs completed in the last 30 minutes during business hours"
```

These rules can be added to `docker/prometheus/alerts.yml` and referenced from the Prometheus config. For Grafana-managed alerts, create them directly in the Grafana UI using the same PromQL expressions.

---

## Compliance and audit trail

For regulated industries, the observability stack provides audit evidence:

- **Trace IDs** link to the full processing lifecycle — which model processed audio, when PII redaction ran, when data was deleted.
- **Structured logs** with `job_id`, `task_id`, and `request_id` are machine-queryable and can be shipped to a log archive.
- **Audit log entries** (`dalston/common/audit.py`) record job creation, completion, and deletion events with `audio_duration` and `tenant_id`.
- The stage breakdown API (`/v1/audio/transcriptions/{id}` with `stages` array) lets clients programmatically verify which pipeline stages ran.

To strengthen the compliance story, include the `trace_id` in API responses so clients can reference it in their own audit logs. The gateway already returns `X-Request-ID` — adding `X-Trace-ID` would complete the picture.

---

## Gaps to address

This guide surfaces several instrumentation gaps that would improve day-to-day utility:

### Audio duration as a span attribute

Currently, `dalston/engine_sdk/runner.py:502` sets `dalston.task_id`, `dalston.engine_id`, and `dalston.stage` on the engine span, but **not** the input audio duration. Without it, "this task took 45 seconds" is meaningless. With it, you can compute realtime factor (45s processing for 8min audio = 5.6x realtime) and benchmark across models.

**Where to add it**: In `dalston/engine_sdk/runner.py`, after loading the task input, call `set_span_attribute("dalston.audio_duration_seconds", ...)` using the duration from the prepare stage output. Also add a corresponding `dalston_engine_audio_duration_seconds` histogram metric.

### Model and language as metric labels

Engine metrics use `engine_id` as the primary label, which implicitly encodes the model. Language is not captured. To do per-language performance analysis:

- Add `language` as a span attribute on engine processing spans (available from the task input config).
- Consider adding it as a metric label on `dalston_engine_task_duration_seconds`, though be cautious about label cardinality (99 languages × N engines).

### Model warm-up vs inference time

The current engine span hierarchy has `engine.download_input` → `engine.process` → `engine.upload_output`. If a container cold-starts and loads a model inside `engine.process`, that latency is mixed with inference time, polluting p95 numbers.

**Fix**: Engine implementations that lazy-load models should create a `engine.model_load` span inside their `process()` method when the model isn't yet loaded. This is an opt-in change per engine, not an SDK change.

### Queue wait time as a span

Queue wait time is captured as a Prometheus metric (`dalston_engine_queue_wait_seconds`) but **not** as a span in the trace. Adding a span between `orchestrator.schedule_task` and `engine.*.process` would make queue wait visible in the Jaeger waterfall. This requires tracking the enqueue timestamp in the trace context and creating a synthetic span when the engine picks up the task.

### EC2 instance ID on spot instances

When running engines on spot instances, add the EC2 instance ID as a resource attribute in the OTel SDK:

```python
import requests

def get_instance_id() -> str | None:
    try:
        token = requests.put(
            "http://169.254.169.254/latest/api/token",
            headers={"X-aws-ec2-metadata-token-ttl-seconds": "21600"},
            timeout=1,
        ).text
        return requests.get(
            "http://169.254.169.254/latest/meta-data/instance-id",
            headers={"X-aws-ec2-metadata-token": token},
            timeout=1,
        ).text
    except Exception:
        return None
```

Pass this to `Resource.create()` in `dalston/telemetry.py:74` so that when a spot instance is reclaimed mid-job, you can distinguish infrastructure failure from model errors in the trace.

### Trace ID in API responses

The gateway returns `X-Request-ID` but not `X-Trace-ID`. Adding the trace ID to response headers (via the correlation middleware) would let API consumers correlate their logs with Dalston's traces — valuable for enterprise integrations and compliance.
