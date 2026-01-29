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
