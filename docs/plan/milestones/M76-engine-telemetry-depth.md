# M76: Engine Telemetry Depth

| | |
|---|---|
| **Goal** | Add fine-grained tracing inside engines so operators can see exactly where inference time is spent — across both batch queue and HTTP processing paths |
| **Duration** | 3-4 days |
| **Dependencies** | M19 (distributed tracing — complete), M20 (metrics — complete), M79 (leaf engine HTTP API — complete) |
| **Deliverable** | Jaeger waterfall shows sub-spans for model loading, VAD, inference, and result parsing within each engine task, regardless of whether the task arrived via Redis queue or direct HTTP |
| **Status** | In progress — ONNX engine instrumented (76.1, 76.2, 76.5) |

## User Story

> *"As an operator investigating a slow transcription job, I can open the Jaeger trace and immediately see whether the bottleneck was model loading (cold start), VAD segmentation, ONNX inference, or S3 I/O — without adding print statements or restarting containers."*

## Motivation

M19 implemented distributed tracing across services (gateway → orchestrator → engine). Each engine task appears as a single `engine.{id}.process` span. But a 178-second transcription of 3.5-minute audio looks the same as a 4-second one — the span just shows total time. Operators cannot distinguish between:

- Cold-start model download from HuggingFace (~10s)
- ONNX session creation and GPU memory allocation (~2s)
- VAD segmentation creating too many segments (~1s)
- Actual inference (variable, depends on audio length and GPU)
- Result parsing and S3 upload (~1s)

**M79 impact:** Engine containers now have two processing paths — batch queue (`runner._process_task()`) and direct HTTP (`run_engine_http()`). Sub-spans and metrics must fire for **both** paths. This is achieved by instrumenting the inference layer (model manager + `OnnxInference`), which is shared by both transports.

This milestone adds sub-spans inside the engine processing path, using the existing OTel infrastructure. Zero overhead when `OTEL_ENABLED=false` (NoOpTracer).

---

## Steps

### 76.1: Engine SDK Runner Sub-Spans

**Files Modified:**

- `dalston/engine_sdk/runner.py` (no changes needed — sub-spans created by engine code)
- `dalston/engine_sdk/http_server.py` (root span added for HTTP path)

**Batch queue span hierarchy (existing runner path):**

```
engine.{id}.process                    ← existing linked span
  ├── engine.download_input            ← existing
  ├── engine.process                   ← existing, now with children from engine code
  │     ├── engine.model_acquire       ← NEW (model manager lock + load)
  │     ├── engine.vad_load            ← NEW (Silero VAD lazy load)
  │     ├── engine.inference           ← NEW (actual recognition call)
  │     └── engine.parse_result        ← NEW (token→word grouping)
  └── engine.upload_output             ← existing
```

**HTTP direct span hierarchy (new M79 path):**

```
engine.{id}.http_process               ← NEW root span for HTTP requests
  ├── engine.model_acquire             ← NEW (same as batch — shared inference layer)
  ├── engine.vad_load                  ← NEW
  ├── engine.inference                 ← NEW
  └── engine.parse_result              ← NEW
```

**Deliverables:**

- No changes to runner.py itself — sub-spans are created by engine code using `dalston.telemetry.create_span()`
- `run_engine_http()` wraps `engine.process()` in an `engine.{id}.http_process` root span
- HTTP-specific metrics: `dalston_engine_http_request_seconds`, `dalston_engine_http_requests_total`
- Engine authors opt-in to granular tracing by adding spans in their inference code
- Existing engines continue working unchanged (single `engine.process` span)

---

### 76.2: ONNX Inference Sub-Spans

**Files Modified:**

- `dalston/engine_sdk/inference/onnx_inference.py`
- `dalston/engine_sdk/managers/onnx.py`

**Deliverables:**

- `OnnxModelManager.acquire()` wrapped in `engine.model_acquire` span
  - Attributes: `model_id`, `cache_hit` (bool), `device`
  - Histogram: `dalston_engine_model_acquire_seconds`
- `_get_or_load_vad()` wrapped in `engine.vad_load` span
  - Attributes: `cache_hit` (bool)
- `_transcribe_with_vad()` / `_transcribe_direct()`: `recognize()` call wrapped in `engine.inference` span
  - Attributes: `audio_duration_s`, `segment_count`, `device`, `mode` (vad/direct), `rtf`
  - Histograms: `dalston_engine_inference_seconds`, `dalston_engine_rtf`, `dalston_engine_vad_segments`
- `_parse_result()` / `_parse_vad_result()` wrapped in `engine.parse_result` span
  - Attributes: `word_count`, `char_count`, `segment_count`

**Span attributes on inference span (set after completion):**

| Attribute | Type | Description |
|---|---|---|
| `dalston.model_id` | string | e.g. `parakeet-onnx-tdt-0.6b-v3` |
| `dalston.device` | string | `cuda` or `cpu` |
| `dalston.audio_duration_s` | float | Input audio length |
| `dalston.segment_count` | int | Number of VAD segments |
| `dalston.rtf` | float | Real-time factor (processing_time / audio_duration) |
| `dalston.word_count` | int | Total words transcribed |
| `dalston.mode` | string | `vad` or `direct` |

---

### 76.3: NeMo and Faster-Whisper Engine Sub-Spans

**Files Modified:**

- `dalston/engine_sdk/inference/nemo_inference.py` (if exists)
- Engine-specific files for other unified engines

**Deliverables:**

- Same span pattern as ONNX: `model_acquire` → `inference` → `parse_result`
- Engine-specific attributes (e.g. `beam_size`, `compute_type` for Faster-Whisper)
- Consistent span naming across all engines for cross-engine comparison in Jaeger

---

### 76.4: Realtime Engine Sub-Spans (Sampled)

**Files Modified:**

- `dalston/realtime_sdk/base.py`
- `dalston/engine_sdk/inference/onnx_inference.py` (direct transcribe path)

**Deliverables:**

- Per-chunk inference spans sampled at 1-in-N rate (configurable via `DALSTON_RT_SPAN_SAMPLE_RATE`, default: 10)
- Session-level summary span with aggregated attributes:
  - `dalston.total_chunks`, `dalston.total_audio_s`, `dalston.avg_chunk_latency_ms`
- VAD endpoint detection spans (not sampled — these are infrequent)

**Why sampling:** Real-time sessions process 10-50 chunks/second. Spanning every chunk would create thousands of spans per session, overwhelming the collector and adding measurable overhead.

---

### 76.5: Metrics Histograms from Spans

**Files Modified:**

- `dalston/engine_sdk/inference/onnx_inference.py` — Emit histograms alongside inference spans
- `dalston/engine_sdk/managers/onnx.py` — Emit model acquire histogram
- `dalston/engine_sdk/http_server.py` — Emit HTTP-specific histograms
- `dalston/metrics.py` — Add engine-level histogram definitions

**New Metrics:**

| Metric | Type | Labels | Description |
|---|---|---|---|
| `dalston_engine_model_acquire_seconds` | Histogram | `engine_id`, `model_id`, `cache_hit` | Time to acquire model |
| `dalston_engine_inference_seconds` | Histogram | `engine_id`, `model_id`, `device` | Pure inference time |
| `dalston_engine_rtf` | Histogram | `engine_id`, `model_id`, `device` | Real-time factor |
| `dalston_engine_vad_segments` | Histogram | `engine_id` | VAD segment count per job |
| `dalston_engine_http_request_seconds` | Histogram | `engine_id`, `endpoint`, `status_code` | HTTP request duration (M79 path only) |
| `dalston_engine_http_requests_total` | Counter | `engine_id`, `endpoint`, `status_code` | HTTP request count (M79 path only) |

**Key design decision:** Metrics are emitted at the **inference layer** (inside `OnnxInference` and `OnnxModelManager`), not in `runner.py`. This ensures they fire for both the batch queue path and the HTTP direct path without duplication. HTTP-specific metrics (`http_request_seconds`, `http_requests_total`) are emitted in `run_engine_http()` since they only apply to the HTTP transport.

**Why both spans and metrics:** Spans are per-request (debug individual slow jobs). Metrics are aggregated (alert when p99 inference time exceeds threshold, dashboard showing RTF trends over time).

---

## Non-Goals

- **Modifying onnx-asr library internals** — We trace around `recognize()`, not inside it. Per-segment inference timing within onnx-asr would require forking the library.
- **GPU profiling** — CUDA kernel-level profiling (nsys/nvprof) is a separate debugging tool, not part of application tracing.
- **Auto-instrumentation of model libraries** — We manually instrument at Dalston's abstraction boundaries, not inside PyTorch/ONNX Runtime.

---

## Verification

### Batch queue path

```bash
# Start with tracing enabled
OTEL_ENABLED=true docker compose --profile observability up -d

# Submit a transcription job
JOB_ID=$(curl -s -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $API_KEY" \
  -F "file=@test.wav" | jq -r '.id')

# Wait for completion, then open Jaeger
open http://localhost:16686

# Search: Service=dalston-gateway, Operation=POST /v1/audio/transcriptions
# Expected waterfall:
#   gateway.create_job                     ~50ms
#   orchestrator.handle_job_created        ~10ms
#   engine.audio-prepare.process           ~2s
#   engine.onnx.process                    ~15s  ← click to expand
#     engine.model_acquire                   ~0.01s (cache hit)
#     engine.vad_load                        ~0.01s (cached)
#     engine.inference                       ~14s
#     engine.parse_result                    ~0.002s
#   engine.final-merger.process            ~1s

# Verify metrics
curl -s http://localhost:9100/metrics | grep dalston_engine_inference_seconds
```

### HTTP direct path

```bash
# Direct HTTP to engine container (M79 endpoint)
curl -X POST http://localhost:9100/v1/transcribe \
  -F "file=@test.wav" \
  -F "model=parakeet-onnx-ctc-0.6b"

# Jaeger should show:
#   engine.onnx.http_process               ~15s  ← root span for HTTP path
#     engine.model_acquire                   ~0.01s (cache hit)
#     engine.vad_load                        ~0.01s (cached)
#     engine.inference                       ~14s
#     engine.parse_result                    ~0.002s

# Verify HTTP-specific metrics
curl -s http://localhost:9100/metrics | grep dalston_engine_http_request_seconds
curl -s http://localhost:9100/metrics | grep dalston_engine_http_requests_total
```

---

## Checkpoint

- [x] ONNX engine emits `model_acquire`, `vad_load`, `inference`, `parse_result` sub-spans
- [x] Span attributes include `model_id`, `device`, `rtf`, `segment_count`
- [ ] Other engines (NeMo, Faster-Whisper) follow same span pattern
- [ ] Realtime spans are sampled to avoid overhead
- [x] Histogram metrics emitted alongside spans
- [x] HTTP path has root span (`engine.{id}.http_process`) and HTTP-specific metrics
- [ ] Jaeger waterfall shows full engine breakdown for a batch job
- [ ] Jaeger waterfall shows full engine breakdown for an HTTP direct request
- [x] Zero overhead when `OTEL_ENABLED=false` confirmed (NoOpTracer + metrics guard)
- [x] No engine `process()` API changes required
