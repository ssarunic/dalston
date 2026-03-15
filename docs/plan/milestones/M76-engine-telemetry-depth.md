# M76: Engine Telemetry Depth

| | |
|---|---|
| **Goal** | Add fine-grained tracing inside engines so operators can see exactly where inference time is spent |
| **Duration** | 2-3 days |
| **Dependencies** | M19 (distributed tracing — complete), M20 (metrics — complete) |
| **Deliverable** | Jaeger waterfall shows sub-spans for model loading, VAD, inference, and result parsing within each engine task |
| **Status** | Not started |

## User Story

> *"As an operator investigating a slow transcription job, I can open the Jaeger trace and immediately see whether the bottleneck was model loading (cold start), VAD segmentation, ONNX inference, or S3 I/O — without adding print statements or restarting containers."*

## Motivation

M19 implemented distributed tracing across services (gateway → orchestrator → engine). Each engine task appears as a single `engine.{id}.process` span. But a 178-second transcription of 3.5-minute audio looks the same as a 4-second one — the span just shows total time. Operators cannot distinguish between:

- Cold-start model download from HuggingFace (~10s)
- ONNX session creation and GPU memory allocation (~2s)
- VAD segmentation creating too many segments (~1s)
- Actual inference (variable, depends on audio length and GPU)
- Result parsing and S3 upload (~1s)

This milestone adds sub-spans inside the engine processing path, using the existing OTel infrastructure. Zero overhead when `OTEL_ENABLED=false` (NoOpTracer).

---

## Steps

### 76.1: Engine SDK Runner Sub-Spans

**Files Modified:**

- `dalston/engine_sdk/runner.py`

**Current span hierarchy:**

```
engine.{id}.process                    ← single span, no children
  ├── engine.download_input            ← exists (S3 download)
  ├── engine.process                   ← exists (wraps engine.process() call)
  └── engine.upload_output             ← exists (S3 upload)
```

**New span hierarchy:**

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

**Deliverables:**

- No changes to runner.py itself — sub-spans are created by engine code using `dalston.telemetry.create_span()`
- Engine authors opt-in to granular tracing by adding spans in their inference code
- Existing engines continue working unchanged (single `engine.process` span)

---

### 76.2: ONNX Inference Sub-Spans

**Files Modified:**

- `dalston/engine_sdk/inference/onnx_inference.py`
- `dalston/engine_sdk/managers/onnx.py`

**Deliverables:**

- `OnnxModelManager.acquire()` wrapped in `engine.model_acquire` span
  - Attributes: `model_id`, `cache_hit` (bool), `load_time_s` (float)
- `_get_or_load_vad()` wrapped in `engine.vad_load` span
  - Attributes: `cache_hit` (bool)
- `vad_ts_model.recognize()` wrapped in `engine.inference` span
  - Attributes: `audio_duration_s`, `segment_count`, `device`
- `_parse_vad_result()` wrapped in `engine.parse_result` span
  - Attributes: `word_count`, `char_count`

**Span attributes on inference span (set after completion):**

| Attribute | Type | Description |
|---|---|---|
| `dalston.model_id` | string | e.g. `parakeet-onnx-tdt-0.6b-v3` |
| `dalston.device` | string | `cuda` or `cpu` |
| `dalston.audio_duration_s` | float | Input audio length |
| `dalston.segment_count` | int | Number of VAD segments |
| `dalston.rtf` | float | Real-time factor (processing_time / audio_duration) |
| `dalston.word_count` | int | Total words transcribed |

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

- `dalston/engine_sdk/runner.py` — Emit histogram metrics alongside spans
- `dalston/metrics.py` — Add engine-level histogram definitions

**New Metrics:**

| Metric | Type | Labels | Description |
|---|---|---|---|
| `dalston_engine_model_acquire_seconds` | Histogram | `engine_id`, `model_id`, `cache_hit` | Time to acquire model |
| `dalston_engine_inference_seconds` | Histogram | `engine_id`, `model_id`, `device` | Pure inference time |
| `dalston_engine_rtf` | Histogram | `engine_id`, `model_id`, `device` | Real-time factor |
| `dalston_engine_vad_segments` | Histogram | `engine_id` | VAD segment count per job |

**Why both spans and metrics:** Spans are per-request (debug individual slow jobs). Metrics are aggregated (alert when p99 inference time exceeds threshold, dashboard showing RTF trends over time).

---

## Non-Goals

- **Modifying onnx-asr library internals** — We trace around `recognize()`, not inside it. Per-segment inference timing within onnx-asr would require forking the library.
- **GPU profiling** — CUDA kernel-level profiling (nsys/nvprof) is a separate debugging tool, not part of application tracing.
- **Auto-instrumentation of model libraries** — We manually instrument at Dalston's abstraction boundaries, not inside PyTorch/ONNX Runtime.

---

## Verification

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

---

## Checkpoint

- [ ] ONNX engine emits `model_acquire`, `vad_load`, `inference`, `parse_result` sub-spans
- [ ] Span attributes include `model_id`, `device`, `rtf`, `segment_count`
- [ ] Other engines (NeMo, Faster-Whisper) follow same span pattern
- [ ] Realtime spans are sampled to avoid overhead
- [ ] Histogram metrics emitted alongside spans
- [ ] Jaeger waterfall shows full engine breakdown for a batch job
- [ ] Zero overhead when `OTEL_ENABLED=false` confirmed via benchmark
- [ ] No engine `process()` API changes required
