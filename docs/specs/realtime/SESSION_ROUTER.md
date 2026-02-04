# Session Router

## Overview

The Session Router manages the real-time worker pool, allocating sessions to workers with available capacity and monitoring worker health.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                            SESSION ROUTER                                        │
│                                                                                  │
│                                                                                  │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │                        WORKER REGISTRY                                   │   │
│   │                                                                          │   │
│   │   Tracks all realtime workers:                                          │   │
│   │   • Endpoint (WebSocket URL)                                            │   │
│   │   • Status (ready / busy / draining)                                    │   │
│   │   • Capacity (max sessions)                                             │   │
│   │   • Active sessions (current count)                                     │   │
│   │   • Loaded models                                                       │   │
│   │   • Last heartbeat                                                      │   │
│   │                                                                          │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │                        SESSION ALLOCATOR                                 │   │
│   │                                                                          │   │
│   │   On new session request:                                               │   │
│   │   1. Filter workers by requirements (model, language)                   │   │
│   │   2. Filter workers with available capacity                             │   │
│   │   3. Select best worker (load balancing)                                │   │
│   │   4. Reserve capacity (increment active_sessions)                       │   │
│   │   5. Return worker endpoint                                             │   │
│   │                                                                          │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │                        HEALTH MONITOR                                    │   │
│   │                                                                          │   │
│   │   • Check heartbeats every 10s                                          │   │
│   │   • Mark workers offline if heartbeat > 30s stale                       │   │
│   │   • Notify Gateway of worker failures                                   │   │
│   │   • Trigger session migration if possible                               │   │
│   │                                                                          │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Responsibilities

### 1. Worker Registration

Workers register on startup and maintain registration via heartbeat.

```
WORKER STARTUP:
  1. Worker starts, loads models
  2. Worker calls Session Router: "I'm ready"
  3. Session Router adds worker to registry
  4. Worker begins sending heartbeats

WORKER SHUTDOWN:
  1. Worker receives shutdown signal
  2. Worker sets status to "draining"
  3. Session Router stops sending new sessions
  4. Existing sessions complete naturally
  5. Worker unregisters and exits
```

### 2. Session Allocation

```
SESSION REQUEST:
  1. Gateway receives WebSocket connection
  2. Gateway calls Session Router: "Need worker for language=en, model=fast"
  3. Session Router finds available worker
  4. Session Router reserves capacity
  5. Session Router returns worker endpoint
  6. Gateway proxies connection to worker

SESSION END:
  1. Worker notifies Session Router: "Session ended"
  2. Session Router releases capacity
```

### 3. Load Balancing

Distribute sessions evenly across workers.

**Strategy: Least Loaded**

```
Select worker with:
  (capacity - active_sessions) = maximum available slots
```

**Strategy: Round Robin with Capacity Check**

```
Rotate through workers, skip if at capacity
```

**Strategy: Weighted**

```
Consider GPU memory, model load time, network proximity
```

### 4. Health Monitoring

```
HEARTBEAT LOOP (every 10s):
  For each registered worker:
    If last_heartbeat > 30s ago:
      Mark worker status = "offline"
      Notify Gateway of affected sessions
      Sessions may reconnect to different worker
```

---

## Redis Data Structures

### Worker Set

```
Key: dalston:realtime:workers
Type: Set
Members: [worker_id, worker_id, ...]
```

### Worker State

```
Key: dalston:realtime:worker:{worker_id}
Type: Hash

{
  "endpoint": "ws://realtime-whisper-1:9000",
  "status": "ready",                          // ready | busy | draining | offline
  "capacity": 4,                              // Max concurrent sessions
  "active_sessions": 2,                       // Current active count
  "gpu_memory_used": "4.2GB",
  "gpu_memory_total": "24GB",
  "models_loaded": "[\"distil-whisper\", \"faster-whisper-large-v3\"]",
  "languages_supported": "[\"en\", \"es\", \"fr\", \"de\", \"auto\"]",
  "last_heartbeat": "2025-01-28T12:00:00Z",
  "started_at": "2025-01-28T10:00:00Z",
  "version": "1.0.0"
}
```

### Worker Sessions

```
Key: dalston:realtime:worker:{worker_id}:sessions
Type: Set
Members: [session_id, session_id, ...]
```

### Session State

```
Key: dalston:realtime:session:{session_id}
Type: Hash

{
  "worker_id": "realtime-whisper-1",
  "status": "active",                         // active | ended | error
  "language": "en",
  "model": "fast",
  "client_ip": "192.168.1.100",
  "started_at": "2025-01-28T12:00:00Z",
  "audio_duration": 45.6,                     // Updated periodically
  "enhance_on_end": "true"
}
```

### Active Sessions Index

```
Key: dalston:realtime:sessions:active
Type: Set
Members: [session_id, ...]
```

---

## Hybrid Mode Storage

When `enhance_on_end=true`, the real-time session's audio is recorded for subsequent batch processing (speaker diarization, LLM cleanup, etc.).

### Storage Architecture

| Layer | Purpose |
|-------|---------|
| **S3** | Permanent storage for audio, transcripts, and metadata |
| **Local temp** | In-flight audio buffering during session |
| **Redis** | Session state (ephemeral, TTL-based) |

### S3 Storage Structure

```
s3://{bucket}/sessions/{session_id}/
├── audio.wav              # Recorded audio (16kHz, 16-bit PCM)
├── transcript.json        # Real-time transcript segments
├── metadata.json          # Session configuration and timing
└── chunks/                # Optional: incremental audio chunks
    ├── 000001.wav
    ├── 000002.wav
    └── ...
```

### Local Temporary Storage

Workers buffer audio locally during the session, then upload to S3 on completion:

```
/tmp/dalston/sessions/{session_id}/
├── audio_buffer.wav       # Growing audio file during session
└── chunks/                # Checkpoint chunks (continuous mode)
```

Local files are deleted after successful S3 upload.

### S3 Object Ownership

| Component | Creates | Writes | Reads |
|-----------|---------|--------|-------|
| Session Router | — | metadata.json (on session start) | metadata.json |
| Real-time Worker | — | audio.wav, transcript.json, chunks/ | — |
| Batch Orchestrator | — | — | All objects |

All S3 writes use the same bucket configured in the environment (`S3_BUCKET`).

### Audio Recording Strategy

Two modes are available, configured per-session:

| Mode | Config | Behavior | Use Case |
|------|--------|----------|----------|
| **End-only** | `checkpoint_interval: 0` | Write complete audio on session end | Short sessions (< 5 min) |
| **Continuous** | `checkpoint_interval: 60` | Write checkpoint every N seconds | Long sessions, fault tolerance |

**Continuous mode benefits:**

- Recoverable on worker crash (audio up to last checkpoint preserved)
- Lower memory usage (chunks flushed to disk)
- Enables mid-session batch enhancement triggers

### Session State (Extended)

```
Key: dalston:realtime:session:{session_id}
Type: Hash
TTL: 300 seconds (extended on activity)

{
  "worker_id": "realtime-whisper-1",
  "tenant_id": "uuid",
  "status": "active",
  "language": "en",
  "model": "fast",
  "client_ip": "192.168.1.100",
  "started_at": "2025-01-28T12:00:00Z",
  "audio_duration": 45.6,
  "enhance_on_end": "true",
  "checkpoint_interval": "60",
  "storage_uri": "s3://bucket/sessions/sess_abc123",
  "last_checkpoint_at": "2025-01-28T12:01:00Z"
}
```

Session state in Redis is ephemeral. Audio and transcripts are persisted to S3.

### Metadata File

**S3 Path**: `s3://{bucket}/sessions/{session_id}/metadata.json`

```json
{
  "session_id": "sess_abc123",
  "tenant_id": "uuid",
  "worker_id": "realtime-whisper-1",
  "started_at": "2025-01-28T12:00:00Z",
  "ended_at": "2025-01-28T12:05:30Z",
  "config": {
    "language": "en",
    "model": "fast",
    "sample_rate": 16000,
    "encoding": "pcm_s16le",
    "enable_vad": true,
    "word_timestamps": true
  },
  "stats": {
    "total_duration_ms": 330000,
    "speech_duration_ms": 285000,
    "segments_count": 42,
    "checkpoints_written": 5
  },
  "storage": {
    "audio_uri": "s3://bucket/sessions/sess_abc123/audio.wav",
    "transcript_uri": "s3://bucket/sessions/sess_abc123/transcript.json"
  }
}
```

### Session End → Batch Enhancement Flow

```
1. Client sends { "type": "end" } or disconnects
2. Worker finalizes audio buffer and uploads to S3:
   - audio.wav → s3://{bucket}/sessions/{session_id}/audio.wav
   - transcript.json → s3://{bucket}/sessions/{session_id}/transcript.json
3. Worker uploads completed metadata.json to S3
4. Worker cleans up local temp files
5. Worker notifies Session Router: session_ended(enhance_requested=true)
6. Session Router creates batch enhancement job in PostgreSQL:
   - job_id = new UUID
   - source_type = "realtime_enhancement"
   - source_session_id = session_id
   - audio_uri = "s3://{bucket}/sessions/{session_id}/audio.wav"
7. Session Router returns enhancement_job_id to Gateway
8. Gateway includes in session.end message to client
9. Batch Orchestrator picks up job, runs enhancement pipeline
10. Enhanced transcript written to s3://{bucket}/jobs/{job_id}/transcript.json
```

### Storage Requirements

All workers require S3 access. No shared filesystem is needed between nodes.

| Component | S3 Access | Local Temp |
|-----------|-----------|------------|
| Real-time Worker | Read/Write | Yes (audio buffering) |
| Batch Worker | Read/Write | Yes (processing) |
| Session Router | Read/Write | No |
| Gateway | Read-only | No |

**Environment Variables:**

```bash
S3_BUCKET=dalston-artifacts
S3_REGION=eu-west-2
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
```

### Cleanup Policy

Session objects in S3 are retained temporarily, then cleaned up via S3 lifecycle rules or a cleanup job:

```yaml
# config/session_router.yaml
storage:
  s3:
    bucket: dalston-artifacts
    region: eu-west-2
  retention:
    completed_sessions: 24h      # Keep for 24 hours after completion
    failed_sessions: 72h         # Keep failed sessions longer for debugging
    enhanced_sessions: 1h        # Delete after batch job completes
  cleanup_interval: 1h           # Run cleanup job every hour
```

**S3 Lifecycle Rule (recommended):**

```json
{
  "Rules": [
    {
      "ID": "cleanup-old-sessions",
      "Prefix": "sessions/",
      "Status": "Enabled",
      "Expiration": { "Days": 7 }
    }
  ]
}
```

---

## API

### Internal API (Gateway → Session Router)

#### Acquire Worker

```python
async def acquire_worker(
    language: str,
    model: str,
    client_ip: str
) -> WorkerAllocation | None:
    """
    Request a worker for a new session.

    Returns:
        WorkerAllocation with endpoint and session_id, or None if no capacity
    """
```

**Response:**

```python
@dataclass
class WorkerAllocation:
    worker_id: str
    endpoint: str           # e.g., "ws://realtime-whisper-1:9000"
    session_id: str         # Newly created session ID
```

#### Release Worker

```python
async def release_worker(session_id: str) -> None:
    """
    Release capacity when session ends.
    """
```

#### List Workers

```python
async def list_workers() -> list[WorkerStatus]:
    """
    Get status of all workers.
    """
```

**Response:**

```python
@dataclass
class WorkerStatus:
    worker_id: str
    endpoint: str
    status: str             # ready | busy | draining | offline
    capacity: int
    active_sessions: int
    models_loaded: list[str]
    last_heartbeat: datetime
```

### Worker API (Worker → Session Router)

#### Register

```python
async def register_worker(
    worker_id: str,
    endpoint: str,
    capacity: int,
    models: list[str],
    languages: list[str]
) -> None:
    """
    Register worker on startup.
    """
```

#### Heartbeat

```python
async def heartbeat(
    worker_id: str,
    active_sessions: int,
    gpu_memory_used: str
) -> None:
    """
    Send periodic heartbeat (every 10s).
    """
```

#### Session Started

```python
async def session_started(
    worker_id: str,
    session_id: str
) -> None:
    """
    Notify that session has begun processing.
    """
```

#### Session Ended

```python
async def session_ended(
    worker_id: str,
    session_id: str,
    duration: float,
    status: str             # completed | error
) -> None:
    """
    Notify that session has ended.
    """
```

#### Unregister

```python
async def unregister_worker(worker_id: str) -> None:
    """
    Remove worker from registry on shutdown.
    """
```

---

## Allocation Algorithm

```python
async def acquire_worker(
    language: str,
    model: str,
    client_ip: str
) -> WorkerAllocation | None:

    # 1. Get all workers
    worker_ids = await redis.smembers("dalston:realtime:workers")

    candidates = []

    for worker_id in worker_ids:
        worker = await redis.hgetall(f"dalston:realtime:worker:{worker_id}")

        # 2. Filter by status
        if worker["status"] not in ("ready", "busy"):
            continue

        # 3. Filter by capacity
        available = int(worker["capacity"]) - int(worker["active_sessions"])
        if available <= 0:
            continue

        # 4. Filter by model
        models_loaded = json.loads(worker["models_loaded"])
        model_name = "distil-whisper" if model == "fast" else "faster-whisper-large-v3"
        if model_name not in models_loaded:
            continue

        # 5. Filter by language
        languages = json.loads(worker["languages_supported"])
        if language != "auto" and language not in languages and "auto" not in languages:
            continue

        candidates.append({
            "worker_id": worker_id,
            "worker": worker,
            "available": available
        })

    if not candidates:
        return None

    # 6. Select best candidate (least loaded)
    best = max(candidates, key=lambda c: c["available"])

    # 7. Reserve capacity (atomic increment)
    await redis.hincrby(
        f"dalston:realtime:worker:{best['worker_id']}",
        "active_sessions",
        1
    )

    # 8. Create session
    session_id = f"sess_{generate_id()}"
    await redis.hset(f"dalston:realtime:session:{session_id}", mapping={
        "worker_id": best["worker_id"],
        "status": "active",
        "language": language,
        "model": model,
        "client_ip": client_ip,
        "started_at": datetime.utcnow().isoformat()
    })

    await redis.sadd("dalston:realtime:sessions:active", session_id)
    await redis.sadd(
        f"dalston:realtime:worker:{best['worker_id']}:sessions",
        session_id
    )

    return WorkerAllocation(
        worker_id=best["worker_id"],
        endpoint=best["worker"]["endpoint"],
        session_id=session_id
    )
```

---

## Health Check Loop

```python
async def health_check_loop():
    while True:
        await asyncio.sleep(10)  # Check every 10 seconds

        worker_ids = await redis.smembers("dalston:realtime:workers")
        now = datetime.utcnow()

        for worker_id in worker_ids:
            worker = await redis.hgetall(f"dalston:realtime:worker:{worker_id}")

            last_heartbeat = datetime.fromisoformat(worker["last_heartbeat"])
            age = (now - last_heartbeat).total_seconds()

            if age > 30 and worker["status"] != "offline":
                # Worker is stale
                await redis.hset(
                    f"dalston:realtime:worker:{worker_id}",
                    "status",
                    "offline"
                )

                # Get affected sessions
                session_ids = await redis.smembers(
                    f"dalston:realtime:worker:{worker_id}:sessions"
                )

                for session_id in session_ids:
                    # Mark session as error
                    await redis.hset(
                        f"dalston:realtime:session:{session_id}",
                        "status",
                        "error"
                    )

                    # Publish event for Gateway
                    await redis.publish("dalston:realtime:events", json.dumps({
                        "type": "worker.offline",
                        "worker_id": worker_id,
                        "session_id": session_id
                    }))
```

---

## Configuration

```yaml
# config/session_router.yaml

redis:
  url: redis://localhost:6379

workers:
  # Health check settings
  heartbeat_interval: 10        # Seconds between heartbeats
  heartbeat_timeout: 30         # Mark offline after this many seconds

  # Allocation settings
  allocation_strategy: least_loaded   # least_loaded | round_robin | weighted

sessions:
  # Session limits
  max_duration: 14400           # 4 hours in seconds
  idle_timeout: 30              # End session after 30s silence

overflow:
  # What to do when no capacity
  strategy: reject              # reject | queue | degrade
  queue_timeout: 30             # Max seconds to wait in queue
  degrade_model: fast           # Fall back to this model
```

---

## Deployment

The Session Router can run:

1. **Embedded in Gateway** — Simpler deployment, single process
2. **Separate Service** — Better for scaling Gateway independently

### Embedded Mode

```python
# gateway/main.py
from dalston.session_router import SessionRouter

router = SessionRouter(redis_url="redis://localhost:6379")

@app.on_event("startup")
async def startup():
    await router.start()

@app.on_event("shutdown")
async def shutdown():
    await router.stop()
```

### Separate Service

```yaml
# docker-compose.yml
services:
  session-router:
    build:
      context: .
      dockerfile: docker/Dockerfile.session-router
    environment:
      - REDIS_URL=redis://redis:6379
    depends_on:
      - redis
```

---

## Metrics

The Session Router exposes metrics for monitoring:

| Metric | Type | Description |
|--------|------|-------------|
| `dalston_realtime_workers_total` | Gauge | Total registered workers |
| `dalston_realtime_workers_ready` | Gauge | Workers accepting sessions |
| `dalston_realtime_capacity_total` | Gauge | Total session capacity |
| `dalston_realtime_capacity_used` | Gauge | Currently used capacity |
| `dalston_realtime_sessions_active` | Gauge | Active sessions |
| `dalston_realtime_sessions_total` | Counter | Total sessions created |
| `dalston_realtime_allocation_time` | Histogram | Time to allocate worker |
| `dalston_realtime_allocation_failures` | Counter | Failed allocations (no capacity) |
