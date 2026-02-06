# M20: Metrics & Dashboards

| | |
|---|---|
| **Goal** | Expose operational metrics and visualize system health in Grafana |
| **Duration** | 3-4 days |
| **Dependencies** | M18 complete (shared logging module), M19 recommended but not required |
| **Deliverable** | Grafana dashboard showing request rates, queue depths, engine latency, and error rates |

## User Story

> *"As an operator, I can open a dashboard and see at a glance whether the system is healthy: how many jobs are queued, how long engines are taking, which components have errors, and how many real-time sessions are active."*

---

## Steps

### 20.1: Prometheus Client Setup

```text
dalston/metrics.py
```

**Deliverables:**

- `configure_metrics(service_name: str)` function that initializes Prometheus client
- Metrics endpoint exposed at `/metrics` on each service (standard Prometheus scrape target)
- `METRICS_ENABLED` environment variable to enable/disable (default: `true`)
- Metric naming convention: `dalston_{service}_{metric_name}_{unit}`
- Common labels on all metrics: `service`, `instance`

**Core Metric Types:**

| Type | Use Case |
| --- | --- |
| Counter | Total requests, total errors, total tasks processed |
| Histogram | Request latency, engine processing time, queue wait time |
| Gauge | Active sessions, queue depth, worker count |

---

### 20.2: Gateway Metrics

**Files Modified:**

- `dalston/gateway/main.py` — Initialize metrics, add `/metrics` endpoint
- `dalston/gateway/middleware/` — Add metrics middleware

**Deliverables:**

| Metric | Type | Labels | Description |
| --- | --- | --- | --- |
| `dalston_gateway_requests_total` | Counter | `method`, `endpoint`, `status_code` | Total HTTP requests |
| `dalston_gateway_request_duration_seconds` | Histogram | `method`, `endpoint` | Request latency |
| `dalston_gateway_jobs_created_total` | Counter | `tenant_id` | Jobs submitted |
| `dalston_gateway_websocket_connections_active` | Gauge | — | Active WebSocket connections |
| `dalston_gateway_upload_bytes_total` | Counter | — | Total bytes uploaded |

---

### 20.3: Orchestrator Metrics

**Files Modified:**

- `dalston/orchestrator/main.py` — Initialize metrics, add `/metrics` endpoint (requires adding a lightweight HTTP server)
- `dalston/orchestrator/handlers.py` — Instrument event handlers
- `dalston/orchestrator/scheduler.py` — Instrument task scheduling

**Deliverables:**

| Metric | Type | Labels | Description |
| --- | --- | --- | --- |
| `dalston_orchestrator_jobs_total` | Counter | `status` | Jobs by final status (completed, failed) |
| `dalston_orchestrator_job_duration_seconds` | Histogram | `stage_count` | Total job duration from creation to completion |
| `dalston_orchestrator_tasks_scheduled_total` | Counter | `engine_id`, `stage` | Tasks pushed to queues |
| `dalston_orchestrator_tasks_completed_total` | Counter | `engine_id`, `status` | Task completions (success, failure) |
| `dalston_orchestrator_events_processed_total` | Counter | `event_type` | Redis events processed |
| `dalston_orchestrator_dag_build_duration_seconds` | Histogram | — | DAG construction time |

---

### 20.4: Engine Metrics

**Files Modified:**

- `dalston/engine_sdk/runner.py` — Add metrics to the task processing loop

**Deliverables:**

| Metric | Type | Labels | Description |
| --- | --- | --- | --- |
| `dalston_engine_tasks_processed_total` | Counter | `engine_id`, `status` | Tasks processed (success, failure) |
| `dalston_engine_task_duration_seconds` | Histogram | `engine_id` | Task processing time (excludes queue wait) |
| `dalston_engine_queue_wait_seconds` | Histogram | `engine_id` | Time between task enqueue and dequeue |
| `dalston_engine_s3_download_seconds` | Histogram | `engine_id` | Input download time |
| `dalston_engine_s3_upload_seconds` | Histogram | `engine_id` | Output upload time |

**Note:** Metrics are collected in the engine SDK — individual engine implementations do not need modification.

---

### 20.5: Session Router and Realtime Metrics

**Files Modified:**

- `dalston/session_router/router.py` — Worker pool and session metrics
- `dalston/session_router/health.py` — Health check metrics
- `dalston/realtime_sdk/base.py` — Session lifecycle metrics

**Deliverables:**

| Metric | Type | Labels | Description |
| --- | --- | --- | --- |
| `dalston_session_router_workers_registered` | Gauge | — | Workers in the pool |
| `dalston_session_router_workers_healthy` | Gauge | — | Workers passing health checks |
| `dalston_session_router_sessions_active` | Gauge | `worker_id` | Active sessions per worker |
| `dalston_session_router_sessions_total` | Counter | `status` | Sessions by outcome (completed, error, timeout) |
| `dalston_session_router_allocation_duration_seconds` | Histogram | — | Session allocation latency |
| `dalston_realtime_session_duration_seconds` | Histogram | — | Total session duration |
| `dalston_realtime_audio_processed_seconds` | Counter | `worker_id` | Cumulative audio processed |
| `dalston_realtime_transcripts_total` | Counter | `type` | Transcripts emitted (partial, final) |

---

### 20.6: Redis Queue Metrics Exporter

```text
dalston/metrics_exporter.py
```

**Deliverables:**

- Lightweight process (or background task in orchestrator) that periodically reads Redis queue depths
- Exposes queue metrics at `/metrics` for Prometheus scraping

| Metric | Type | Labels | Description |
| --- | --- | --- | --- |
| `dalston_queue_depth` | Gauge | `engine_id` | Tasks waiting in each engine queue |
| `dalston_queue_oldest_task_age_seconds` | Gauge | `engine_id` | Age of oldest task in queue |
| `dalston_redis_connected` | Gauge | — | Redis connectivity (1 = connected, 0 = disconnected) |

---

### 20.7: Prometheus and Grafana in Docker Compose

**Files Modified:**

- `docker-compose.yml` — Add Prometheus and Grafana services

**Files Created:**

```text
docker/prometheus/
└── prometheus.yml              # Scrape configuration

docker/grafana/
├── provisioning/
│   ├── datasources/
│   │   └── prometheus.yml      # Auto-configure Prometheus datasource
│   └── dashboards/
│       └── dashboards.yml      # Auto-provision dashboard directory
└── dashboards/
    └── dalston-overview.json   # Pre-built overview dashboard
```

**Deliverables:**

- Prometheus container scraping all Dalston services
- Grafana container with auto-provisioned Prometheus datasource
- Services behind Docker Compose profile (`--profile monitoring`)
- Prometheus UI at port 9090, Grafana UI at port 3001 (avoids conflict with web console on 3000)

**Docker Compose Addition:**

```yaml
prometheus:
  image: prom/prometheus:v2.50.0
  volumes:
    - ./docker/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml
  ports:
    - "9090:9090"
  profiles:
    - monitoring

grafana:
  image: grafana/grafana:10.3.0
  volumes:
    - ./docker/grafana/provisioning:/etc/grafana/provisioning
    - ./docker/grafana/dashboards:/var/lib/grafana/dashboards
  ports:
    - "3001:3000"
  environment:
    - GF_SECURITY_ADMIN_PASSWORD=dalston
    - GF_AUTH_ANONYMOUS_ENABLED=true
    - GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer
  profiles:
    - monitoring
```

---

### 20.8: Grafana Dashboard

**File Created:**

- `docker/grafana/dashboards/dalston-overview.json`

**Dashboard Panels:**

| Row | Panels |
| --- | --- |
| **Request Overview** | Requests/sec, Error rate (%), Avg latency |
| **Batch Pipeline** | Jobs in progress, Queue depth by engine, Job completion rate |
| **Engine Performance** | Processing time by engine (p50, p95, p99), Task throughput, Failure rate by engine |
| **Real-time Sessions** | Active sessions, Workers healthy/total, Session duration distribution |
| **System Health** | Redis connectivity, Queue oldest task age, Error logs/sec |

**Variables:**

- `$service` — Filter by service (gateway, orchestrator, engine-*)
- `$engine_id` — Filter by engine type
- `$interval` — Auto-adjusted time aggregation

---

## Verification

```bash
# Start services with monitoring
docker compose --profile monitoring up -d
METRICS_ENABLED=true docker compose up -d

# Verify metrics endpoints
curl http://localhost:8000/metrics | head -20
# → dalston_gateway_requests_total{method="POST",endpoint="/v1/audio/transcriptions",...} 0

# Submit some jobs to generate metrics
for i in $(seq 1 5); do
  curl -s -X POST http://localhost:8000/v1/audio/transcriptions \
    -F "file=@test.wav" > /dev/null
done

# Check Prometheus targets
# → http://localhost:9090/targets
# All targets should show "UP"

# Open Grafana dashboard
# → http://localhost:3001
# → Login: admin / dalston
# → Navigate to "Dalston Overview" dashboard
# → Verify panels show data for the 5 submitted jobs

# Verify queue depth metric
curl -s http://localhost:9090/api/v1/query?query=dalston_queue_depth | jq '.data.result'
```

---

## Checkpoint

- [ ] **Gateway** exposes `/metrics` with request counters, latency histograms, and active connections
- [ ] **Orchestrator** exposes `/metrics` with job/task counters and duration histograms
- [ ] **Engines** expose `/metrics` with processing time and queue wait histograms (via SDK)
- [ ] **Session Router** exposes `/metrics` with worker pool and session gauges
- [ ] **Redis queue exporter** reports queue depth and oldest task age
- [ ] **Prometheus** scrapes all services and shows targets as "UP"
- [ ] **Grafana** dashboard shows request rates, engine performance, queue depths, and session counts
- [ ] **Monitoring disabled by default** — no overhead when `--profile monitoring` is not used
- [ ] **No code changes** required in engine `process()` methods

**Previous**: [M19: Distributed Tracing](M19-distributed-tracing.md)
