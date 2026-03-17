# M80: Engine Control Plane (Push-Based Unified Dispatch)

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | Replace the dual dispatch model (pull-from-stream for batch, push-via-WS for realtime) with a single push-based control plane where the orchestrator places all work on engine instances via typed, stage-specific HTTP APIs |
| **Duration**       | 3–4 weeks                                                    |
| **Dependencies**   | M63 (Engine Unification), M64 (Registry Unification), M66 (Session Router Consolidation) |
| **Deliverable**    | Stage-specific engine APIs, orchestrator-driven placement for both modes, fleet status API, deprecation of stream-pull dispatch |
| **Status**         | Not Started                                                  |

## User Story

> *"As a platform operator, I want the orchestrator to decide exactly which engine instance handles each piece of work — batch or realtime — so that GPU utilization is globally optimal and I have one place to observe and control scheduling."*

---

## Motivation

Dalston currently has two fundamentally different dispatch paths:

```
Batch:     Orchestrator → Redis Stream → Engine PULLS task (poll loop)
Realtime:  Gateway → Orchestrator allocates → Client WS pushed to Engine
```

This split causes real problems:

1. **Orchestrator is blind to batch execution.** It pushes a task ID into a Redis Stream and hopes an engine picks it up. It doesn't know which instance claimed it, how long it's been waiting, or whether the instance it hoped for is actually the one processing it.

2. **No instance-level batch placement.** The stream is keyed by `engine_id`, not by instance. All instances of the same engine_id compete in a consumer group. The orchestrator can't direct a task to the instance with the right model loaded or the most batch headroom.

3. **Admission control is split-brain.** The per-engine `AdmissionController` enforces QoS locally (RT reservation, batch cap), but the orchestrator can't see or coordinate with it. It queues batch tasks to an engine that will NACK them, creating pointless redelivery cycles.

4. **Two codepaths to maintain.** The `EngineRunner` has ~500 lines of stream polling, stale claiming, consumer group management, and deferred-task recovery. The realtime path has its own allocation and WS proxy logic. Both need independent testing, monitoring, and failure handling.

5. **The generic `TaskRequest` envelope is untyped.** Every engine receives the same `{"task_id", "config": dict, "stage": str}` blob regardless of what it does. The engine has to deserialize, validate, and type-check internally. The orchestrator constructs the right config dict without compile-time guarantees. A transcription engine is a transcription service — it should accept `TranscribeRequest` and return `Transcript`, not parse a generic bag of keys.

6. **The "fleet scheduler" problem (M79) is a symptom.** M79 proposed adding queue depth tracking, admission status in heartbeats, and instance hints — all to compensate for the orchestrator's blindness. A push model eliminates the need for most of that machinery because the orchestrator *is* the scheduler.

### How Parakeet NIM does it

NVIDIA Parakeet NIM exposes a purpose-built ASR API:

- `POST /v1/audio/transcriptions` — multipart form with `file` + `language`, returns transcript JSON synchronously
- `WS /` — realtime streaming with ASR-specific session config (`language`, `word_boosting_list`, `enable_speaker_diarization`)
- `GET /v1/health/ready` — health check
- Ports: HTTP on 9000, gRPC on 50051

Key design choices in NIM:
- **No generic task interface** — the endpoint *is* the domain: transcription in, transcript out
- **No URL/URI input** — audio must be uploaded in the request body (max 25 MB) or streamed via gRPC/WebSocket
- **No async/webhook support** — processing is synchronous. The caller blocks until the transcript is ready. For long files, NIM uses `true-ofl` profiles that VAD-segment audio into ~30s chunks and parallelize inference internally
- **No job queue** — NIM is a stateless inference endpoint. Orchestration (async jobs, retries, DAG) is the caller's responsibility

This is the right model for an *engine*. An engine is a domain-specific inference service. The orchestration layer (Dalston's orchestrator) handles async jobs, DAG scheduling, retries, and fleet placement. The engine just does its job and returns the result.

### Dalston vs NIM: what changes and what stays

| Concern | NIM | Dalston Engine (after M80) |
|---------|-----|---------------------------|
| API style | Domain-specific REST | Domain-specific REST (per stage) |
| Input | File upload in body | S3 URI (orchestrator pre-stages artifacts) |
| Output | Synchronous JSON response | Synchronous JSON response (orchestrator handles async) |
| Long files | Internal VAD chunking | Orchestrator chunks via prepare stage |
| Async jobs | Not supported (caller's problem) | Orchestrator manages jobs, polls for completion |
| Webhooks | None | Orchestrator publishes durable events to Redis |
| Model management | Container-baked or NIM cache | Dalston model registry + S3 + engine cache |
| Fleet placement | External (k8s, Triton) | Orchestrator FleetPlacer |

The key difference: NIM engines use file upload in the request body. Dalston engines use S3 URIs because audio is already staged by the prepare step. This avoids re-uploading multi-GB files over HTTP — the engine fetches from S3 directly, which it already does today.

### Why Redis stays

Redis is not going away. It remains the orchestrator's backbone for:

- **Job/task state** — status, metadata, DAG progress
- **Durable events** — `task.started`, `task.completed`, pub/sub notifications
- **Heartbeats** — engine registration and health (engines push heartbeats to Redis; orchestrator reads them)
- **Session state** — realtime session tracking
- **Coordination** — leader election, distributed locks

What changes: Redis Streams stop being the batch dispatch mechanism. The orchestrator pushes work directly to engines via their typed HTTP API. Engines can optionally continue reporting heartbeats and capacity to Redis for the orchestrator to read.

---

## Architecture

### Current (dual dispatch, generic task envelope)

```
┌──────────────┐  {"task_id","config":dict}  ┌───────────────┐  XREADGROUP  ┌──────────────┐
│ Orchestrator │ ───────────────────────────→│ Redis Stream   │←────────────│ Engine       │
│              │                              │ (per engine_id)│             │ (poll loop)  │
└──────────────┘                              └───────────────┘             └──────────────┘

┌──────────────┐   allocate     ┌───────────────┐    WS connect    ┌──────────────┐
│ Gateway      │ ──────────────→│ Orchestrator   │                  │ RT Engine    │
│ (WS proxy)   │                │ (allocator)   │                  │ (WS server)  │
└──────────────┘                └───────────────┘                  └──────────────┘
```

### After M80 (unified push, typed per-stage APIs)

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           ORCHESTRATOR                                    │
│                                                                          │
│   ┌──────────────┐    ┌──────────────┐    ┌──────────────────────────┐   │
│   │ DAG Scheduler│    │ Fleet Placer │    │ Instance Tracker          │   │
│   │              │───→│              │───→│ (capacity, models, health)│   │
│   │ "task ready" │    │ "place on    │    │                          │   │
│   │              │    │  instance X" │    │ Source: Redis heartbeats  │   │
│   └──────────────┘    └──────┬───────┘    │ + control API responses  │   │
│                              │            └──────────────────────────┘   │
│                              │                                           │
└──────────────────────────────┼───────────────────────────────────────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
     POST /transcribe   POST /diarize     POST /transcribe
              │                │                │
     ┌────────▼───┐   ┌───────▼────┐   ┌───────▼────┐
     │ Transcribe │   │ Diarize    │   │ Unified    │
     │ Engine     │   │ Engine     │   │ Engine     │
     │            │   │            │   │            │
     │ :9100      │   │ :9100      │   │ :9100      │
     │            │   │            │   │            │
     │ POST       │   │ POST       │   │ POST       │
     │ /transcribe│   │ /diarize   │   │ /transcribe│
     │            │   │            │   │ POST       │
     │ GET /status│   │ GET /status│   │ /diarize   │
     │ GET /health│   │ GET /health│   │ WS /session│
     │            │   │            │   │ GET /status│
     └────────────┘   └────────────┘   │ GET /health│
                                       └────────────┘
```

Each engine type exposes only the endpoints relevant to its stage. A transcription engine serves `POST /transcribe`. A diarization engine serves `POST /diarize`. A unified engine serves both plus `WS /session`.

---

## Stage-Specific API Contracts

Each engine exposes a typed HTTP API matching its pipeline stage. Common endpoints (`/health`, `/status`, `/cancel/{task_id}`) are shared across all engine types via the engine SDK.

### Common Endpoints (all engines)

```
GET  /health             → {"status": "healthy"}
GET  /metrics            → Prometheus format
GET  /status             → Full instance state (see below)
DELETE /cancel/{task_id} → Cancel in-progress task
```

`GET /status` response (common to all engines):

```json
{
  "instance": "fw-abc123",
  "engine_id": "faster-whisper",
  "stage": "transcribe",
  "status": "processing",
  "active_tasks": ["task-001", "task-002"],
  "active_sessions": ["sess-001"],
  "capacity": 6,
  "available": 3,
  "models_loaded": ["large-v3-turbo"],
  "gpu_memory_used_mb": 4096,
  "admission": {
    "can_accept_batch": true,
    "can_accept_rt": true,
    "rt_reservation": 2,
    "batch_max_inflight": 4
  }
}
```

### Prepare Engine

```
POST /prepare
```

Request (`PreparationRequest` — already exists in `pipeline_types.py`):

```json
{
  "task_id": "task-001",
  "job_id": "job-001",
  "audio_uri": "s3://dalston-artifacts/jobs/job-001/upload/audio.wav",
  "target_sample_rate": 16000,
  "target_channels": 1,
  "target_encoding": "pcm_s16le",
  "normalize_volume": true,
  "detect_speech_regions": false,
  "split_channels": false,
  "timeout_seconds": 120
}
```

Response (`PreparationResponse`):

```json
{
  "task_id": "task-001",
  "channel_files": [
    {
      "artifact_id": "prepared-ch0",
      "uri": "s3://dalston-artifacts/jobs/job-001/prepare/ch0.wav",
      "format": "wav",
      "duration": 127.5,
      "sample_rate": 16000,
      "channels": 1,
      "bit_depth": 16
    }
  ],
  "split_channels": false,
  "speech_regions": null,
  "speech_ratio": null,
  "engine_id": "audio-prepare",
  "skipped": false,
  "warnings": []
}
```

### Transcribe Engine

```
POST /transcribe
```

Request (`TranscriptionRequest`):

```json
{
  "task_id": "task-002",
  "job_id": "job-001",
  "audio_uri": "s3://dalston-artifacts/jobs/job-001/prepare/ch0.wav",
  "loaded_model_id": "large-v3-turbo",
  "language": null,
  "task": "transcribe",
  "word_timestamps": true,
  "timestamp_granularity": "word",
  "vocabulary": ["Dalston", "NVIDIA"],
  "vad_filter": true,
  "temperature": 0.0,
  "beam_size": 5,
  "prompt": null,
  "channel": null,
  "timeout_seconds": 300
}
```

Response (`Transcript`):

```json
{
  "task_id": "task-002",
  "text": "Hello from Dalston.",
  "segments": [
    {
      "id": "seg-0",
      "start": 0.0,
      "end": 1.5,
      "text": "Hello from Dalston.",
      "words": [
        {"text": "Hello", "start": 0.0, "end": 0.4, "confidence": 0.98},
        {"text": "from", "start": 0.45, "end": 0.65, "confidence": 0.97},
        {"text": "Dalston.", "start": 0.7, "end": 1.5, "confidence": 0.95}
      ],
      "confidence": 0.97
    }
  ],
  "language": "en",
  "language_confidence": 0.99,
  "duration": 1.5,
  "timestamp_granularity": "word",
  "alignment_method": "attention",
  "engine_id": "faster-whisper",
  "warnings": []
}
```

### Align Engine

```
POST /align
```

Request (`AlignmentRequest`):

```json
{
  "task_id": "task-003",
  "job_id": "job-001",
  "audio_uri": "s3://dalston-artifacts/jobs/job-001/prepare/ch0.wav",
  "transcript": { "...": "Transcript from transcribe stage" },
  "loaded_model_id": "wav2vec2-large-960h",
  "target_granularity": "word",
  "return_char_alignments": false,
  "return_phoneme_alignments": false,
  "timeout_seconds": 120
}
```

Response (`AlignmentResponse`):

```json
{
  "task_id": "task-003",
  "segments": [],
  "text": "Hello from Dalston.",
  "language": "en",
  "word_timestamps": true,
  "alignment_confidence": 0.94,
  "unaligned_words": [],
  "unaligned_ratio": 0.0,
  "granularity_achieved": "word",
  "engine_id": "phoneme-align",
  "skipped": false,
  "warnings": []
}
```

### Diarize Engine

```
POST /diarize
```

Request (`DiarizationRequest`):

```json
{
  "task_id": "task-004",
  "job_id": "job-001",
  "audio_uri": "s3://dalston-artifacts/jobs/job-001/prepare/ch0.wav",
  "segments": [],
  "loaded_model_id": "pyannote-speaker-diarization-3.1",
  "num_speakers": null,
  "min_speakers": null,
  "max_speakers": null,
  "detect_overlap": true,
  "exclusive": false,
  "timeout_seconds": 180
}
```

Response (`DiarizationResponse`):

```json
{
  "task_id": "task-004",
  "turns": [
    {"speaker": "SPEAKER_00", "start": 0.0, "end": 3.2, "confidence": 0.91},
    {"speaker": "SPEAKER_01", "start": 3.5, "end": 7.8, "confidence": 0.88}
  ],
  "speakers": ["SPEAKER_00", "SPEAKER_01"],
  "num_speakers": 2,
  "overlap_duration": 0.0,
  "overlap_ratio": 0.0,
  "engine_id": "pyannote-4.0",
  "skipped": false,
  "warnings": []
}
```

### PII Detect Engine

```
POST /pii-detect
```

Request:

```json
{
  "task_id": "task-005",
  "job_id": "job-001",
  "transcript": { "...": "Transcript with speaker labels" },
  "loaded_model_id": "gliner-multitask-large-v0.5",
  "entity_types": null,
  "confidence_threshold": 0.5,
  "timeout_seconds": 60
}
```

Response (`PIIDetectionResponse`):

```json
{
  "task_id": "task-005",
  "entities": [
    {
      "entity_type": "credit_card_number",
      "category": "pci",
      "start_offset": 42, "end_offset": 58,
      "start_time": 3.2, "end_time": 5.1,
      "confidence": 0.92,
      "speaker": "SPEAKER_01",
      "redacted_value": "****7890",
      "original_text": "4532 0123 4567 7890"
    }
  ],
  "redacted_text": "...",
  "entity_count_by_type": {"credit_card_number": 1},
  "entity_count_by_category": {"pci": 1},
  "processing_time_ms": 150,
  "engine_id": "pii-presidio",
  "skipped": false,
  "warnings": []
}
```

### Audio Redact Engine

```
POST /redact
```

Request:

```json
{
  "task_id": "task-006",
  "job_id": "job-001",
  "audio_uri": "s3://dalston-artifacts/jobs/job-001/prepare/ch0.wav",
  "pii_entities": [],
  "redaction_mode": "beep",
  "buffer_ms": 50,
  "timeout_seconds": 60
}
```

Response (`RedactionResponse`):

```json
{
  "task_id": "task-006",
  "redacted_audio_uri": "s3://dalston-artifacts/jobs/job-001/redact/redacted.wav",
  "redaction_mode": "beep",
  "buffer_ms": 50,
  "entities_redacted": 1,
  "redaction_map": [{"start": 3.15, "end": 5.15, "entity_type": "credit_card_number"}],
  "engine_id": "audio-redactor",
  "skipped": false,
  "warnings": []
}
```

### Realtime Session (unchanged)

```
WS /session?model=large-v3-turbo&language=en&sample_rate=16000&...
```

Existing WebSocket protocol — already push-based, already typed. No changes needed.

### Input via S3 URI, not file upload

Unlike NIM (which accepts file upload in the request body, max 25 MB), Dalston engines receive an `audio_uri` pointing to S3. This is intentional:

- Audio is already in S3 from the prepare stage — re-uploading it over HTTP wastes bandwidth
- No file size limit — S3 handles multi-GB files; the engine streams from S3
- Multiple stages read the same audio (transcribe, align, diarize) — S3 is shared storage, not per-request upload
- The orchestrator doesn't touch audio data — it passes URIs, keeping the placement API lightweight

### Async via orchestrator, not engine webhooks

Like NIM, the engine API is **synchronous** — the engine processes the request and returns the result in the HTTP response. The orchestrator handles async:

1. Orchestrator calls `POST /transcribe` on the engine
2. Engine processes and returns `Transcript` in the response body
3. Orchestrator publishes `task.completed` event to Redis
4. Orchestrator advances the DAG (queues dependent tasks)

For long-running tasks (multi-minute transcriptions), the orchestrator holds the HTTP connection open with a generous timeout. If the connection drops, the orchestrator retries on the same or different instance.

This is simpler than webhooks because:
- No callback URL management
- No engine-to-orchestrator network path needed (engines don't need to reach the orchestrator)
- Retry is trivial — just re-POST
- The orchestrator already tracks task state in Redis

**Timeout handling**: The orchestrator sets `timeout_seconds` in the request. The engine enforces it locally. If the engine exceeds the timeout, it returns 504. The orchestrator marks the task failed and can retry on another instance.

---

## Steps

### 80.1: Stage-Specific Request/Response Types for Control API

**Files modified:**

- `dalston/engine_sdk/control_types.py` *(new)* — Pydantic models for control API requests/responses per stage
- `dalston/common/pipeline_types.py` — minor: ensure all stage types are re-exportable

**Deliverables:**

Define the typed request/response models for each stage's HTTP API. These wrap the existing `pipeline_types.py` models with the additional fields needed for HTTP dispatch (`task_id`, `job_id`, `audio_uri`, `timeout_seconds`).

```python
"""Typed request/response models for engine control API."""

from pydantic import BaseModel

from dalston.common.pipeline_types import (
    AlignmentResponse,
    DiarizationResponse,
    PIIDetectionResponse,
    PreparationResponse,
    RedactionResponse,
    Transcript,
)


class _EngineRequest(BaseModel):
    """Common fields for all engine requests."""
    task_id: str
    job_id: str
    timeout_seconds: int = 300


class PrepareRequest(_EngineRequest):
    audio_uri: str
    target_sample_rate: int = 16000
    target_channels: int = 1
    target_encoding: str = "pcm_s16le"
    normalize_volume: bool = True
    detect_speech_regions: bool = False
    split_channels: bool = False


class TranscribeRequest(_EngineRequest):
    audio_uri: str
    loaded_model_id: str | None = None
    language: str | None = None
    task: str = "transcribe"
    word_timestamps: bool = True
    timestamp_granularity: str = "word"
    vocabulary: list[str] | None = None
    vad_filter: bool = True
    temperature: float | list[float] = 0.0
    beam_size: int | None = None
    prompt: str | None = None
    channel: int | None = None


class AlignRequest(_EngineRequest):
    audio_uri: str
    transcript: dict  # Serialized Transcript from transcribe stage
    loaded_model_id: str | None = None
    target_granularity: str = "word"
    return_char_alignments: bool = False
    return_phoneme_alignments: bool = False


class DiarizeRequest(_EngineRequest):
    audio_uri: str
    segments: list[dict] = []  # From transcribe/align
    loaded_model_id: str | None = None
    num_speakers: int | None = None
    min_speakers: int | None = None
    max_speakers: int | None = None
    detect_overlap: bool = True
    exclusive: bool = False


class PIIDetectRequest(_EngineRequest):
    transcript: dict  # Serialized Transcript with speaker labels
    loaded_model_id: str | None = None
    entity_types: list[str] | None = None
    confidence_threshold: float = 0.5


class RedactRequest(_EngineRequest):
    audio_uri: str
    pii_entities: list[dict]
    redaction_mode: str = "beep"
    buffer_ms: int = 50


# Response types: reuse existing pipeline_types directly.
# Each stage handler returns its native response type:
#   prepare   → PreparationResponse
#   transcribe → Transcript
#   align     → AlignmentResponse
#   diarize   → DiarizationResponse
#   pii_detect → PIIDetectionResponse
#   redact    → RedactionResponse
#
# The control API wraps these in a standard envelope:

class EngineResponse(BaseModel):
    """Standard envelope for all engine responses."""
    task_id: str
    status: str = "completed"  # completed | failed
    data: dict  # Serialized stage-specific response
    error: str | None = None

# Stage → (request_type, response_type, endpoint_path) mapping
STAGE_API_MAP: dict[str, tuple[type[_EngineRequest], type, str]] = {
    "prepare": (PrepareRequest, PreparationResponse, "/prepare"),
    "transcribe": (TranscribeRequest, Transcript, "/transcribe"),
    "align": (AlignRequest, AlignmentResponse, "/align"),
    "diarize": (DiarizeRequest, DiarizationResponse, "/diarize"),
    "pii_detect": (PIIDetectRequest, PIIDetectionResponse, "/pii-detect"),
    "audio_redact": (RedactRequest, RedactionResponse, "/redact"),
}
```

The `STAGE_API_MAP` lets the orchestrator and engine SDK generically wire stages to endpoints without switch statements.

---

### 80.2: Engine Control API Server

**Files modified:**

- `dalston/engine_sdk/control_api.py` *(new)* — HTTP control API server with typed routing
- `dalston/engine_sdk/runner.py` — extract `_process_task()` into reusable method; start control API server

**Deliverables:**

A lightweight HTTP server (extending the existing metrics server on :9100) that routes to stage-specific handlers based on the engine's declared stage.

```python
class ControlAPIServer:
    """Stage-specific HTTP control API for engine instances."""

    def __init__(
        self,
        engine: Engine,
        admission: AdmissionController | None,
        stage: str,
        process_fn: Callable,  # Extracted from EngineRunner
    ):
        self._engine = engine
        self._admission = admission
        self._stage = stage
        self._process_fn = process_fn
        self._active_tasks: dict[str, Future] = {}
        self._executor = ThreadPoolExecutor(max_workers=8)

        # Resolve request type and path from stage
        if stage in STAGE_API_MAP:
            req_type, _, self._endpoint_path = STAGE_API_MAP[stage]
            self._request_type = req_type
        else:
            raise ValueError(f"Unknown stage: {stage}")

    def route(self, method: str, path: str, body: bytes) -> tuple[int, dict]:
        """Route an HTTP request to the appropriate handler."""
        if method == "GET" and path == "/health":
            return 200, {"status": "healthy"}
        if method == "GET" and path == "/status":
            return 200, self._get_status()
        if method == "POST" and path == self._endpoint_path:
            return self._handle_stage_request(body)
        if method == "DELETE" and path.startswith("/cancel/"):
            task_id = path.split("/cancel/", 1)[1]
            return self._handle_cancel(task_id)
        return 404, {"error": "not_found"}

    def _handle_stage_request(self, body: bytes) -> tuple[int, dict]:
        """Handle a typed stage request."""
        # Parse and validate request
        request = self._request_type.model_validate_json(body)

        # Check admission
        if self._admission and not self._admission.admit_batch():
            return 409, {"error": "at_capacity"}

        # Submit for processing
        future = self._executor.submit(
            self._process_fn, request
        )
        self._active_tasks[request.task_id] = future

        # Block until complete (orchestrator holds connection)
        try:
            result = future.result(timeout=request.timeout_seconds)
            del self._active_tasks[request.task_id]
            return 200, EngineResponse(
                task_id=request.task_id,
                status="completed",
                data=result.model_dump(),
            ).model_dump()
        except TimeoutError:
            del self._active_tasks[request.task_id]
            return 504, {"error": "timeout", "task_id": request.task_id}
        except Exception as e:
            del self._active_tasks[request.task_id]
            return 500, EngineResponse(
                task_id=request.task_id,
                status="failed",
                data={},
                error=str(e),
            ).model_dump()
        finally:
            if self._admission:
                self._admission.release_batch()
```

Key design decisions:
- **Synchronous request/response** — the orchestrator holds the HTTP connection open. No webhooks needed.
- **Typed validation at the boundary** — `self._request_type.model_validate_json(body)` validates the request against the stage-specific Pydantic model. Malformed requests fail with 422 before touching the engine.
- **Admission check before processing** — returns 409 immediately if at capacity, so the orchestrator can try another instance.

The `_process_fn` is extracted from `EngineRunner._process_one_task()` — the same logic that currently runs after stream polling (S3 download, `engine.process()`, S3 upload). The only change: input comes from the HTTP request instead of Redis metadata.

---

### 80.3: Engine `process()` Method Refactor

**Files modified:**

- `dalston/engine_sdk/base.py` — update `Engine.process()` signature to accept typed request
- `dalston/engine_sdk/runner.py` — adapt existing stream-based processing to use typed requests
- Individual engine implementations in `engines/` — update `process()` signatures

**Deliverables:**

Today, `Engine.process()` receives a generic `TaskRequest` with `config: dict`. After this step:

```python
# Before (generic)
class Engine:
    def process(self, request: TaskRequest, ctx: BatchTaskContext) -> TaskResponse:
        config = request.config  # Untyped dict
        model_id = config.get("loaded_model_id")  # Hope it's there
        ...

# After (typed per stage)
class TranscribeEngine(Engine):
    def process(self, request: TranscribeRequest, ctx: BatchTaskContext) -> Transcript:
        model_id = request.loaded_model_id  # Type-checked
        language = request.language  # Type-checked
        ...

class DiarizeEngine(Engine):
    def process(self, request: DiarizeRequest, ctx: BatchTaskContext) -> DiarizationResponse:
        num_speakers = request.num_speakers  # Type-checked
        ...
```

Migration path:
1. Add typed `process()` method to each engine class
2. Keep the generic `process(TaskRequest)` as a compatibility shim that constructs the typed request from `config`
3. The control API calls the typed method directly
4. The stream polling path (still active in `push` mode) calls via the compatibility shim
5. Once stream polling is removed (`push_only` mode), remove the compatibility shim

This is the largest step in terms of files touched but each engine is a small, mechanical change — extract config dict access into typed fields.

---

### 80.4: Orchestrator FleetPlacer with Typed Dispatch

**Files modified:**

- `dalston/orchestrator/placer.py` *(new)* — `FleetPlacer` that selects instance + pushes typed request
- `dalston/orchestrator/scheduler.py` — add push-based dispatch path alongside existing stream path
- `dalston/common/registry.py` — add `endpoint` field to `EngineRecord`

**Deliverables:**

```python
class FleetPlacer:
    """Places tasks on specific engine instances via typed control API."""

    def __init__(self, registry: UnifiedEngineRegistry, http: httpx.AsyncClient):
        self._registry = registry
        self._http = http

    async def place_task(
        self, task: Task, stage: str, request_body: dict
    ) -> PlacementResult:
        """Select best instance and push typed request."""
        candidates = await self._registry.get_available(
            interface="batch", engine_id=task.engine_id,
        )
        if not candidates:
            return PlacementResult(placed=False, reason="no_healthy_instance")

        # Rank by: model warmth → batch headroom → RT pressure
        target_model = request_body.get("loaded_model_id")
        candidates.sort(
            key=lambda r: (
                target_model not in (r.models_loaded or []) if target_model else False,
                -r.available_capacity,
                r.active_realtime,
            )
        )

        # Resolve stage endpoint path
        _, _, endpoint_path = STAGE_API_MAP[stage]

        # Try candidates in ranked order
        for instance in candidates:
            try:
                resp = await self._http.post(
                    f"{instance.endpoint}{endpoint_path}",
                    json=request_body,
                    timeout=request_body.get("timeout_seconds", 300) + 30,
                )
                if resp.status_code == 200:
                    return PlacementResult(
                        placed=True,
                        instance=instance.instance,
                        response=resp.json(),
                    )
                if resp.status_code == 409:
                    continue  # At capacity, try next
                if resp.status_code == 422:
                    # Validation error — don't retry, it'll fail everywhere
                    return PlacementResult(
                        placed=False,
                        reason=f"validation_error: {resp.text}",
                    )
            except httpx.RequestError:
                continue

        return PlacementResult(placed=False, reason="all_instances_rejected")
```

The orchestrator now constructs a typed request body (using `STAGE_API_MAP`) instead of writing a generic config dict to Redis. The request is validated by the engine's Pydantic model on receipt.

The scheduler dispatch mode flag:

```python
class Scheduler:
    def __init__(self, ..., dispatch_mode: str = "stream"):
        self._dispatch_mode = dispatch_mode

    async def dispatch_task(self, task, stage, config, ...):
        if self._dispatch_mode in ("push", "push_only"):
            # Build typed request from DAG config
            req_type, _, _ = STAGE_API_MAP[stage]
            request_body = req_type(
                task_id=task.id,
                job_id=task.job_id,
                **config,
            ).model_dump()

            result = await self._placer.place_task(task, stage, request_body)
            if result.placed:
                return result
            if self._dispatch_mode == "push_only":
                raise PlacementError(result.reason)
            # Fallback to stream in "push" mode
            logger.warning("push_failed_falling_back", reason=result.reason)

        await self._queue_to_stream(task, config)
```

---

### 80.5: Engine Registration with Control Endpoint

**Files modified:**

- `dalston/engine_sdk/runner.py` — include control API endpoint URL in registration
- `dalston/realtime_sdk/base.py` — same for RT engines
- `dalston/common/registry.py` — persist and expose `endpoint` field in `EngineRecord`

**Deliverables:**

When an engine registers (via Redis heartbeat), it now includes its control API URL:

```python
record = EngineRecord(
    instance=self.instance,
    engine_id=self.engine_id,
    # ... existing fields ...
    endpoint=f"http://{hostname}:{self.metrics_port}",
)
```

The `endpoint` field is what `FleetPlacer` uses to reach the engine. For Docker Compose, this is the container hostname. For k8s, it's the pod IP. For local dev, it's `localhost:{port}`.

---

### 80.6: Orchestrator Task Lifecycle (Push Mode)

**Files modified:**

- `dalston/orchestrator/placer.py` — add placement tracking
- `dalston/orchestrator/handlers.py` — handle push-mode task lifecycle

**Deliverables:**

In push mode, the orchestrator gets the task result synchronously from the HTTP response. It no longer needs to wait for durable events to learn about completion:

```python
async def handle_task_ready(self, task: Task, stage: str, config: dict):
    """Dispatch a ready task and handle the result."""
    result = await self._placer.place_task(task, stage, request_body)

    if result.placed and result.response:
        response = result.response
        if response["status"] == "completed":
            # Task completed — advance DAG immediately
            await self._handle_task_completed(task, response["data"])
        else:
            # Task failed — mark failed, no retry
            await self._handle_task_failed(task, response.get("error"))
    elif not result.placed:
        # No instance accepted — handle based on mode
        ...
```

The orchestrator still publishes durable events to Redis (for observability, webhooks, and the gateway to poll), but it no longer depends on them for DAG advancement.

Placement state tracked in Redis for recovery:

```python
# Redis hash: dalston:placement:{task_id}
{
    "instance": "fw-abc123",
    "placed_at": "2026-03-17T...",
    "status": "placed",  # placed | completed | failed
}
```

If the orchestrator crashes mid-placement, on restart it can detect stale placements and re-dispatch.

---

### 80.7: Deprecate Stream Polling (Feature Flag)

**Files modified:**

- `dalston/engine_sdk/runner.py` — make stream polling conditional
- `dalston/orchestrator/scheduler.py` — make dispatch mode configurable

**Deliverables:**

`DALSTON_DISPATCH_MODE` environment variable:

| Value | Orchestrator | Engine |
|-------|-------------|--------|
| `stream` (default) | Pushes to Redis Stream | Polls Redis Stream |
| `push` | Typed HTTP to engine, falls back to stream | Control API + stream polling |
| `push_only` | Typed HTTP only | Control API only, no polling |

Migration path:
1. Deploy engines with control API (80.1–80.3) — they still poll streams
2. Switch orchestrator to `push` — typed HTTP dispatch, stream fallback
3. Observe: verify `dalston_placement_fallback_total` is zero
4. Switch to `push_only` — stream polling loop disabled

---

### 80.8: Fleet Status API & Metrics

**Files modified:**

- `dalston/gateway/api/v1/fleet.py` *(new)* — `GET /v1/fleet/status`
- `dalston/gateway/api/v1/__init__.py` — register router
- `dalston/orchestrator/metrics.py` — Prometheus gauges for placement

**Deliverables:**

Fleet status endpoint:

```json
GET /v1/fleet/status
{
  "dispatch_mode": "push",
  "engines": {
    "faster-whisper": {
      "stage": "transcribe",
      "api_endpoint": "/transcribe",
      "instances": 2,
      "healthy_instances": 2,
      "total_capacity": 12,
      "active_batch": 3,
      "active_rt": 2,
      "available": 7,
      "instances_detail": [
        {
          "instance": "fw-abc123",
          "endpoint": "http://fw-abc123:9100",
          "status": "processing",
          "active_batch": 2,
          "active_rt": 1,
          "capacity": 6,
          "models_loaded": ["large-v3-turbo"],
          "admission": {
            "can_accept_batch": true,
            "can_accept_rt": true,
            "rt_reservation": 2,
            "batch_max_inflight": 4
          }
        }
      ]
    }
  }
}
```

Prometheus metrics:

```
dalston_placement_total{engine_id, stage, outcome="completed|rejected|failed|timeout"}
dalston_placement_latency_seconds{engine_id, stage}
dalston_placement_fallback_total{engine_id}
dalston_fleet_dispatch_mode{mode="stream|push|push_only"}
```

---

## Non-Goals

- **gRPC transport** — HTTP is sufficient for the dispatch latencies involved. gRPC adds protobuf compilation to every engine build. Can be added as an alternative transport behind the same interface if needed.
- **File upload in request body** — Unlike NIM (max 25 MB), Dalston engines use S3 URIs. Audio is already staged by the prepare step. Re-uploading over HTTP wastes bandwidth and limits file size.
- **Engine-side webhooks/callbacks** — The synchronous request/response model is simpler. The orchestrator holds the connection and handles async job state. Engines don't need outbound network access to the orchestrator.
- **Removing Redis entirely** — Redis remains for job state, events, heartbeats, and session tracking. Only the stream-pull dispatch path is replaced.
- **Changing the realtime WebSocket protocol** — The client-to-engine WS protocol is unchanged. What changes is how the orchestrator *places* the session (richer status queries), not the session itself.
- **Multi-orchestrator placement coordination** — This milestone assumes a single orchestrator (or leader-elected). Sharded placement is a separate concern.
- **Engine autoscaling** — Using placement rejection rates to trigger scale-up. Valuable, but separate milestone.

---

## Risks

### Orchestrator becomes a single point of failure for dispatch

**Mitigation:**
- In `push` mode (not `push_only`), stream fallback ensures tasks are dispatched if orchestrator restarts
- Engine heartbeats continue independently of the control API
- Orchestrator restart recovery: re-place tasks with stale placement records
- Long-term: orchestrator HA via leader election (existing pattern)

### Synchronous HTTP ties up orchestrator connections during long tasks

**Mitigation:**
- The orchestrator uses an async HTTP client (`httpx.AsyncClient`). Each placement is a coroutine, not a thread. Hundreds can be in-flight concurrently.
- Connection timeout is `task_timeout + 30s` — generous but bounded.
- If the connection drops (engine crash, network), the orchestrator retries on another instance.

### Engine control API adds attack surface

**Mitigation:**
- Control API is on the internal network (same as Redis, S3). Not exposed externally.
- No authentication needed on internal control plane (same trust model as current Redis streams).
- Input validation via Pydantic models at the boundary — rejects malformed requests before reaching engine code.

---

## Deployment

**Rollout strategy (zero-downtime):**

1. Deploy control types + API server (80.1, 80.2, 80.5) — engines serve typed endpoints alongside stream polling
2. Deploy engine `process()` refactor (80.3) — typed methods with compatibility shim
3. Deploy orchestrator with `DALSTON_DISPATCH_MODE=push` (80.4, 80.6) — typed HTTP dispatch with stream fallback
4. Monitor: check `dalston_placement_fallback_total` — should be near zero
5. Deploy with `DALSTON_DISPATCH_MODE=push_only` (80.7) — stream polling disabled
6. Deploy fleet API + metrics (80.8) — observability

Each step is independently deployable. Rollback at any step: set `DALSTON_DISPATCH_MODE=stream`.

---

## Verification

```bash
make dev

# 1. Verify typed control API on transcribe engine
curl -s http://localhost:9100/status | jq .

# 2. Push a transcription directly to engine
curl -s -X POST http://localhost:9100/transcribe \
  -H "Content-Type: application/json" \
  -d '{
    "task_id": "test-1",
    "job_id": "test-job-1",
    "audio_uri": "s3://dalston-artifacts/test/audio.wav",
    "loaded_model_id": "large-v3-turbo",
    "language": "en",
    "word_timestamps": true,
    "timeout_seconds": 300
  }' | jq .

# 3. Verify validation rejects bad requests
curl -s -X POST http://localhost:9100/transcribe \
  -H "Content-Type: application/json" \
  -d '{"bad_field": true}' | jq .
# Should return 422

# 4. Submit job via API and verify push placement
export DALSTON_DISPATCH_MODE=push
curl -s -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -F file=@test.wav | jq .job_id

docker compose logs orchestrator | grep "task_placed"

# 5. Verify fleet status
curl -s http://localhost:8000/v1/fleet/status \
  -H "Authorization: Bearer $DALSTON_API_KEY" | jq .

# 6. Verify no stream fallback
curl -s http://localhost:9090/api/v1/query?query=dalston_placement_fallback_total | jq .
```

---

## Checkpoint

- [ ] Stage-specific request/response types defined in `control_types.py`
- [ ] `STAGE_API_MAP` maps stages to typed endpoints
- [ ] Engine control API server routes to stage-specific handlers
- [ ] Typed validation (Pydantic) at engine API boundary — 422 on bad input
- [ ] Admission check returns 409 before processing
- [ ] Engine `process()` accepts typed request, returns typed response
- [ ] Compatibility shim preserves stream-based processing during migration
- [ ] `FleetPlacer` constructs typed request, dispatches to best instance
- [ ] Orchestrator gets task result synchronously from HTTP response
- [ ] Engine registration includes `endpoint` field
- [ ] Placement state tracked in Redis for crash recovery
- [ ] `DALSTON_DISPATCH_MODE` flag controls stream vs push vs push_only
- [ ] `GET /v1/fleet/status` returns fleet snapshot with stage + endpoint info
- [ ] Prometheus metrics for placement outcomes, latency, and fallback count
- [ ] Existing tests pass (`make test`)
- [ ] Mixed-load benchmark shows no regression
- [ ] Stream fallback metric is zero before switching to `push_only`
