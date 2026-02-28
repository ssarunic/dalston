# M37: Capacity Management

Rationalizes engine concurrency parameters and implements proper capacity enforcement for realtime sessions.

## Background

The `max_concurrency` field in engine.yaml was intended to describe how many parallel work units an engine instance can handle. However:

- **Batch engines**: The SDK runner processes tasks sequentially (one at a time). Parallelism is achieved via horizontal scaling (multiple container instances).
- **Realtime engines**: Already have capacity enforcement via `DALSTON_MAX_SESSIONS` env var.

The field was metadata-only for batch engines and redundant for realtime engines.

## Phases Completed

### Phase 1: Remove max_concurrency from Batch Engines

Removed `max_concurrency` from all batch engine schemas since:

- Batch engines process one task at a time
- Parallelism = horizontal scaling (Docker replicas, Kubernetes pods)
- The field was informational only, never enforced

### Phase 2: Enforce Realtime Session Limits

Changed default `DALSTON_MAX_SESSIONS` from 4 to 2 as a conservative starting point.
Capacity enforcement was already implemented at two levels:

1. Session Router (allocator.py): Checks `active_sessions > capacity` before allocation
2. Worker (realtime_sdk/base.py): Rejects connections when `len(sessions) >= max_sessions`

---

## Phase 3: Empirical Capacity Tuning (Future)

Determine realistic `DALSTON_MAX_SESSIONS` values through load testing.

### Objective

Find the maximum concurrent sessions per realtime worker before quality degrades.

### Test Harness Requirements

1. **Load generator**: Python script that opens N concurrent WebSocket connections
2. **Audio source**: Pre-recorded test files or synthetic audio (silence, speech, music)
3. **Metrics collection**: Prometheus scraping during test runs
4. **Quality measurement**: Compare transcription output against ground truth (optional)

### Test Protocol

```bash
# 1. Deploy single realtime worker
docker compose up -d stt-rt-transcribe-faster-whisper

# 2. Run load test with increasing concurrency
for N in 1 2 3 4 6 8; do
    python scripts/load_test_realtime.py \
        --concurrency $N \
        --duration 60 \
        --audio-file test_audio.wav \
        --output results_$N.json
done

# 3. Analyze results
python scripts/analyze_load_test.py results_*.json
```

### Metrics to Capture

| Metric | Source | Threshold |
|--------|--------|-----------|
| Transcription latency (p50, p95, p99) | `dalston_realtime_chunk_latency_seconds` | p99 < 300ms |
| GPU utilization | `nvidia_smi` or `DCGM` exporter | < 90% |
| GPU memory | Worker heartbeat / nvidia-smi | < 90% VRAM |
| Word Error Rate | Ground truth comparison | Degradation < 5% |
| Connection failures | Load test client | 0% |

### Deliverables

1. `scripts/load_test_realtime.py` - WebSocket load generator
2. `scripts/analyze_load_test.py` - Results analyzer
3. Per-engine capacity recommendations in engine variant YAMLs
4. Updated `DALSTON_MAX_SESSIONS` defaults per engine type

### Timeline

- Load test harness: 2-3 days
- Testing each engine variant: 1 day per variant
- Documentation: 1 day

---

## Phase 4: Dynamic Capacity Admission (Future)

Implement adaptive session admission based on real-time metrics.

### Objective

Instead of static limits, dynamically adjust admission based on observed load.

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Session Router                          │
│  ┌─────────────────────────────────────────────────────┐   │
│  │              Admission Controller                    │   │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐             │   │
│  │  │ Hard    │  │ Soft    │  │ Dynamic │             │   │
│  │  │ Limit   │→ │ Limit   │→ │ Check   │→ Accept/    │   │
│  │  │ Check   │  │ Check   │  │         │  Reject     │   │
│  │  └─────────┘  └─────────┘  └─────────┘             │   │
│  └─────────────────────────────────────────────────────┘   │
│                           ↑                                 │
│                    Metrics Query                            │
└───────────────────────────┼─────────────────────────────────┘
                            │
                   ┌────────┴────────┐
                   │   Prometheus    │
                   │  ┌───────────┐  │
                   │  │ latency   │  │
                   │  │ gpu_util  │  │
                   │  │ errors    │  │
                   │  └───────────┘  │
                   └─────────────────┘
```

### Decision Logic

```python
async def should_admit_session(worker_id: str) -> AdmissionResult:
    """Determine if a new session should be admitted to a worker."""

    worker = await registry.get_worker(worker_id)

    # 1. Hard limit - never exceed (prevents OOM)
    if worker.active_sessions >= worker.hard_max:
        return AdmissionResult(
            allowed=False,
            reason="hard_limit_exceeded",
            retry_after=30,
        )

    # 2. Query recent metrics
    metrics = await prometheus.query_worker_metrics(
        worker_id,
        window="30s",
    )

    # 3. Latency-based soft limit
    if metrics.latency_p99 > 300:  # ms
        return AdmissionResult(
            allowed=False,
            reason="latency_degraded",
            retry_after=10,
        )

    # 4. GPU utilization soft limit
    if metrics.gpu_utilization > 0.85:
        return AdmissionResult(
            allowed=False,
            reason="gpu_saturated",
            retry_after=10,
        )

    # 5. Error rate soft limit
    if metrics.error_rate > 0.05:
        return AdmissionResult(
            allowed=False,
            reason="error_rate_elevated",
            retry_after=30,
        )

    return AdmissionResult(allowed=True)
```

### Required Components

1. **Prometheus integration in Session Router**
   - Query endpoint for worker metrics
   - Caching layer (avoid per-request queries)

2. **Enhanced worker metrics export**
   - Per-session latency histogram
   - GPU metrics (via DCGM or nvidia-smi)
   - Error rate counter

3. **Admission result API**
   - Return `Retry-After` header on 503
   - Include reason in error response for client retry logic

4. **Circuit breaker pattern**
   - If worker repeatedly trips soft limits, temporarily remove from pool
   - Automatic recovery when metrics improve

### Configuration

```yaml
# session_router.yaml
admission:
  hard_max_sessions: 4          # Never exceed per worker
  soft_limits:
    latency_p99_ms: 300         # Reject if exceeded
    gpu_utilization: 0.85       # Reject if exceeded
    error_rate: 0.05            # Reject if exceeded
  metrics_window: 30s           # Lookback window for metrics
  circuit_breaker:
    failure_threshold: 3        # Trips after N rejections
    recovery_timeout: 60s       # Time before retry
```

### Migration Path

1. Deploy with soft limits disabled (metrics-only mode)
2. Collect baseline metrics for 1-2 weeks
3. Enable soft limits with conservative thresholds
4. Tune thresholds based on observed patterns

### Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Prometheus unavailable | Fall back to hard limits only |
| Metrics lag causes oscillation | Use 30s window, add hysteresis |
| False positives reject good traffic | Start conservative, tune over time |
| Complexity increases debugging | Detailed logging of admission decisions |

### Timeline

- Prometheus integration: 2-3 days
- Admission controller: 3-4 days
- Circuit breaker: 2 days
- Testing and tuning: 1 week
- Documentation: 1 day

**Total: ~3 weeks**

---

## References

- [dalston/session_router/allocator.py](../../../dalston/session_router/allocator.py) - Current allocation logic
- [dalston/realtime_sdk/base.py](../../../dalston/realtime_sdk/base.py) - Worker capacity enforcement
- [M20: Metrics Dashboards](./M20-metrics-dashboards.md) - Prometheus setup
