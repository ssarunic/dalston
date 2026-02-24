# Observability Improvements TODO

Actionable work items to close the gaps identified in the
[Observability Workflow Guide](guides/observability-workflow.md).

---

## 1. EC2 instance type on spans (OTEL resource detector)

**Why**: Running the same engine on g4dn.xlarge vs g5.xlarge gives different
performance. Without instance type on traces, you can't tell whether a slow
task was the model or the hardware.

**What**: Add the `opentelemetry-resource-detector-aws` package and merge its
detected attributes into the OTEL `Resource` at startup.

**Where**: `dalston/telemetry.py` — `configure_tracing()`, line 73–74.

**How**:

```python
from opentelemetry.sdk.resources import Resource, SERVICE_NAME

resource = Resource.create({SERVICE_NAME: service_name})

# Merge AWS EC2 resource attributes when running on EC2.
# Populates host.type (instance type), host.id (instance ID),
# cloud.provider, cloud.platform, cloud.availability_zone.
# No-ops gracefully when not on EC2 (local dev, CI).
try:
    from opentelemetry.resource.detector.aws import AwsEc2ResourceDetector
    resource = resource.merge(AwsEc2ResourceDetector().detect())
except Exception:
    pass  # Not on EC2 or package not installed
```

**Dependencies**: Add `opentelemetry-resource-detector-aws` to the project's
optional dependencies (e.g., `pyproject.toml` under `[project.optional-dependencies]`
or the engine Dockerfiles' `requirements.txt`).

**Outcome**: Every span from every engine automatically includes:
- `host.type` = `g5.xlarge`
- `host.id` = `i-0abc123def456`
- `cloud.availability_zone` = `us-east-1a`

Jaeger queries like `host.type=g5.xlarge` instantly filter by hardware.

**Risks**: The detector calls EC2 IMDS (Instance Metadata Service) once at startup.
On IMDSv2-only instances, it issues a PUT for a session token then a GET — both
with a 1-second timeout. If IMDS is disabled or firewalled (e.g., in ECS with
`awsvpc` networking and metadata disabled), the call silently times out and
the resource attributes are simply absent. No impact on functionality.

---

## 2. `INSTANCE_TYPE` environment variable for metrics and logs

**Why**: OTEL resource detectors tag traces but don't help Prometheus metrics
or structlog. For PromQL queries like "p95 engine duration grouped by instance
type", you need it as a metric label. For log filtering, you need it in
structlog context.

**What**: Read `INSTANCE_TYPE` from environment, bind it to structlog context,
and add it as a label to the engine metrics that matter for benchmarking.

**Where**:
- `dalston/engine_sdk/runner.py` — `__init__()`, line 121–127
- `dalston/metrics.py` — `_init_engine_metrics()`, line 175
- `dalston/metrics.py` — `observe_engine_task_duration()`, `inc_engine_tasks()`
- Docker/ECS task definitions — set `INSTANCE_TYPE` env var

**How**:

### runner.py — bind at startup

```python
self.instance_type = os.environ.get("INSTANCE_TYPE", "unknown")

# Bind to structlog so every log line includes instance_type
structlog.contextvars.bind_contextvars(instance_type=self.instance_type)
```

### metrics.py — add label to benchmarking metrics

Add `instance_type` label to `dalston_engine_task_duration_seconds` and
`dalston_engine_tasks_processed_total`. These are the two metrics used for
cross-hardware comparison. Do NOT add it to every metric — keep cardinality
bounded.

```python
_engine_metrics["task_duration_seconds"] = Histogram(
    "dalston_engine_task_duration_seconds",
    "Task processing time (excludes queue wait)",
    ["engine_id", "instance_type"],
    buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
)

_engine_metrics["tasks_processed_total"] = Counter(
    "dalston_engine_tasks_processed_total",
    "Tasks processed",
    ["engine_id", "status", "instance_type"],
)
```

Update all call sites (`observe_engine_task_duration`, `inc_engine_tasks`) to
accept and pass through `instance_type`.

### docker-compose.yml — set the env var

For local dev, add to the `x-observability-env` anchor:

```yaml
INSTANCE_TYPE: ${INSTANCE_TYPE:-local}
```

For AWS, set it in the ECS task definition or EC2 launch template user data.

**Cardinality impact**: Bounded. You'll have ~3–4 instance types (g4dn.xlarge,
g5.xlarge, g5.2xlarge, local). Combined with ~6 engine_ids, that's ~24 time
series per metric — negligible.

---

## 3. Audio duration as a span attribute and metric

**Why**: "This task took 45 seconds" is meaningless without knowing the input
size. With audio duration, you can compute realtime factor (45s processing /
480s audio = 10.7x realtime) and benchmark across models and hardware.

**What**: Set `dalston.audio_duration_seconds` on the engine processing span.
Add a `dalston_engine_audio_duration_seconds` histogram metric.

**Where**: `dalston/engine_sdk/runner.py` — `_process_task()`, after loading
task input (line 515–519).

**How**:

### Span attribute

After `task_input = self._load_task_input(...)`:

```python
audio_duration = task_input.config.get("audio_duration_seconds")
if audio_duration is not None:
    dalston.telemetry.set_span_attribute(
        "dalston.audio_duration_seconds", float(audio_duration)
    )
```

The audio duration is set by the `audio-prepare` stage and passed forward in
the task input config. If it's not available (e.g., the prepare stage didn't
compute it), the attribute is simply absent.

### Histogram metric

Add to `dalston/metrics.py`:

```python
_engine_metrics["audio_duration_seconds"] = Histogram(
    "dalston_engine_audio_duration_seconds",
    "Input audio duration in seconds",
    ["engine_id"],
    buckets=(10, 30, 60, 120, 300, 600, 1800, 3600),
)
```

Add helper function and call it alongside the task duration metric in runner.py.

### Derived metric (Grafana)

Once both `dalston_engine_task_duration_seconds` and
`dalston_engine_audio_duration_seconds` are present, compute realtime factor:

```promql
# Realtime factor by engine (higher = faster)
histogram_quantile(0.50, sum(rate(dalston_engine_audio_duration_seconds_bucket[5m])) by (engine_id, le))
/
histogram_quantile(0.50, sum(rate(dalston_engine_task_duration_seconds_bucket[5m])) by (engine_id, le))
```

**Prerequisite**: Verify that the `audio-prepare` engine writes
`audio_duration_seconds` into the task config that downstream engines receive.
If not, add it to the prepare engine output → orchestrator task input flow.

---

## 4. Language as a span attribute

**Why**: Per-language performance analysis. Some models are faster/more accurate
on English than Mandarin. Without language on spans, this analysis requires
joining Jaeger spans with PostgreSQL job metadata.

**What**: Set `dalston.language` on the engine processing span.

**Where**: `dalston/engine_sdk/runner.py` — `_process_task()`, after loading
task input.

**How**:

```python
language = task_input.config.get("language")
if language:
    dalston.telemetry.set_span_attribute("dalston.language", language)
```

**NOT a Prometheus label**: Language has ~99 possible values. Combined with
~6 engine_ids and ~4 instance_types, that's ~2,400 time series per metric.
This is borderline for Prometheus. Keep language as a span attribute only
(queryable in Jaeger) and leave it off Prometheus metrics. If per-language
metrics are needed later, use a recording rule that aggregates to a top-N
subset.

---

## 5. Model warm-up vs inference separation

**Why**: When an engine cold-starts (spot instance launch, container restart),
the first task includes model loading time (often 10–30s for large whisper
models). This pollutes p95/p99 metrics and makes it look like the engine is
slow.

**What**: Engine implementations that lazy-load models should create an
`engine.model_load` span inside their `process()` method when the model
isn't yet loaded.

**Where**: Individual engine implementations (not the SDK). Example:
`engines/stt/batch-transcribe-faster-whisper/engine.py`.

**How**: This is an opt-in pattern, not an SDK change. Document it in the
engine developer guide:

```python
class FasterWhisperEngine(Engine):
    def __init__(self):
        self._model = None

    def process(self, task_input: TaskInput) -> TaskOutput:
        if self._model is None:
            with dalston.telemetry.create_span("engine.model_load"):
                self._model = WhisperModel("large-v3", device="cuda")

        # Actual inference
        segments, info = self._model.transcribe(task_input.audio_path)
        ...
```

**Outcome**: In the Jaeger waterfall, the first task shows:

```
engine.faster-whisper.process (42s)
  ├── engine.model_load (30s)     ← only on cold start
  ├── engine.download_input (0.5s)
  ├── engine.process (11s)        ← actual inference
  └── engine.upload_output (0.5s)
```

Subsequent tasks don't have the `model_load` span, so p50 stays clean.

**Action items**:
- [ ] Add `engine.model_load` span to `faster-whisper` engine
- [ ] Add `engine.model_load` span to `whisperx` engine
- [ ] Add `engine.model_load` span to `pyannote` diarization engines
- [ ] Document the pattern in the engine developer guide

---

## 6. Queue wait time as a trace span

**Why**: Queue wait is captured as a Prometheus metric
(`dalston_engine_queue_wait_seconds`) but is invisible in the Jaeger
waterfall. When a job takes 5 minutes and the engine processes it in 30
seconds, the remaining 4.5 minutes is queue wait — but you can't see that
in the trace.

**What**: Create a synthetic `engine.queue_wait` span between the
orchestrator's `schedule_task` span and the engine's processing span.

**Where**: `dalston/engine_sdk/runner.py` — `_process_task()`, before the
main processing span.

**How**:

The enqueue timestamp is already stored in task metadata (`enqueued_at`).
Use it to create a span with the correct start time:

```python
from opentelemetry.trace import SpanKind
import datetime

enqueued_at_str = task_metadata.get("enqueued_at")
if enqueued_at_str and dalston.telemetry.is_tracing_enabled():
    enqueued_at = datetime.datetime.fromisoformat(enqueued_at_str)
    dequeued_at = datetime.datetime.now(datetime.UTC)
    queue_wait_ns = int((dequeued_at - enqueued_at).total_seconds() * 1e9)

    # Create a span that represents the time spent waiting in the queue
    tracer = dalston.telemetry.get_tracer()
    queue_span = tracer.start_span(
        "engine.queue_wait",
        start_time=int(enqueued_at.timestamp() * 1e9),
        kind=SpanKind.INTERNAL,
        attributes={
            "dalston.engine_id": self.engine_id,
            "dalston.queue_wait_seconds": (dequeued_at - enqueued_at).total_seconds(),
        },
    )
    queue_span.end(end_time=int(dequeued_at.timestamp() * 1e9))
```

**Caveat**: The queue wait span's parent must be the orchestrator's
`schedule_task` span. This requires the trace context injected by the
orchestrator to be active when creating the span. The current code already
restores trace context via `span_from_context()` — create the queue wait
span inside that context, before the processing span.

---

## 7. Prometheus alerting rules

**Why**: The guide proposes three alerts but they aren't actually configured.
Without alerts, the metrics are passive — you only notice problems when
someone looks at the dashboard.

**What**: Create `docker/prometheus/alerts.yml` with the three rules and
reference it from the Prometheus config.

**Where**:
- New file: `docker/prometheus/alerts.yml`
- Update: `docker/prometheus/prometheus.yml` — add `rule_files` directive

**How**:

### docker/prometheus/alerts.yml

```yaml
groups:
  - name: dalston
    rules:
      - alert: QueueDepthHigh
        expr: dalston_queue_depth > 20
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Engine {{ $labels.engine_id }} queue depth at {{ $value }}"
          description: >
            The queue for engine {{ $labels.engine_id }} has had more than 20
            pending tasks for 5 minutes. Workers may be down or overwhelmed.

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
          description: >
            Engine {{ $labels.engine_id }} has failed more than 5% of tasks
            over the last 10 minutes. Check container logs and model status.

      - alert: NoJobsProcessed
        expr: |
          sum(increase(dalston_orchestrator_jobs_total[30m])) == 0
          and ON() (hour() >= 8 and hour() <= 18)
        for: 30m
        labels:
          severity: warning
        annotations:
          summary: "No jobs completed in the last 30 minutes during business hours"
          description: >
            The orchestrator has not completed any jobs in 30 minutes during
            business hours (08:00–18:00 UTC). Check orchestrator, Redis, and
            engine health.
```

### docker/prometheus/prometheus.yml — add rule_files

```yaml
rule_files:
  - /etc/prometheus/alerts.yml
```

### docker-compose.yml — mount the alerts file

Add to the prometheus service volumes:

```yaml
- ./docker/prometheus/alerts.yml:/etc/prometheus/alerts.yml:ro
```

---

## 8. Update the observability workflow guide

**Why**: After implementing items 1–7, the "Gaps to address" section of the
guide should be updated to reflect what's been completed, and new dashboard
panels should be documented.

**What**: Update `docs/guides/observability-workflow.md`:
- Move completed gaps to a "Recently added" section
- Add instance type to the cost dashboard PromQL examples
- Add realtime factor panel to the per-engine performance section

**Where**: `docs/guides/observability-workflow.md`

---

## Implementation order

Recommended sequence based on value/effort:

| Priority | Item | Effort | Value |
|----------|------|--------|-------|
| 1 | Audio duration on spans (#3) | Small | High — unlocks realtime factor |
| 2 | `INSTANCE_TYPE` env var (#2) | Small | High — enables hardware comparison |
| 3 | EC2 resource detector (#1) | Small | Medium — automatic trace enrichment |
| 4 | Language on spans (#4) | Small | Medium — per-language analysis |
| 5 | Alerting rules (#7) | Small | Medium — proactive monitoring |
| 6 | Queue wait span (#6) | Medium | Medium — trace visibility |
| 7 | Model warm-up separation (#5) | Medium | Medium — cleaner metrics, per-engine |
| 8 | Guide update (#8) | Small | Low — do last |

Items 1–5 can be done in parallel (no dependencies). Item 8 should be done
after all others.
