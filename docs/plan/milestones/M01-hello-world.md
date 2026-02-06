# M1: "Hello World" Transcription

| | |
|---|---|
| **Goal** | Prove the complete batch flow works end-to-end with stubs |
| **Duration** | 2-3 days |
| **Dependencies** | None (first milestone) |
| **Deliverable** | Submit file → get stub transcript back |
| **Status** | Completed (January 2026) |

## User Story

> *"As a developer, I can POST an audio file and get a (fake) transcript back, proving the system works."*

---

## Steps

### 1.1: Project Bootstrap

Create the repository structure:

```text
dalston/
├── pyproject.toml
├── docker-compose.yml
├── .env.example
└── dalston/
    ├── __init__.py
    ├── config.py              # Pydantic settings
    └── common/
        ├── __init__.py
        ├── redis.py           # Redis client factory
        └── models.py          # Shared Pydantic models
```

**Deliverables:**

- `config.py` with Redis URL, S3 config, database URL via environment variables
- `redis.py` with async connection factory
- Core enums: `JobStatus`, `TaskStatus`
- Core models: `Job`, `Task` as Pydantic models

---

### 1.2: Gateway Skeleton

```text
dalston/gateway/
├── __init__.py
├── main.py                    # FastAPI app entry point
├── api/v1/
│   ├── __init__.py
│   ├── router.py              # Mount all v1 routes
│   └── transcription.py       # POST/GET /v1/audio/transcriptions
└── services/
    └── jobs.py                # Job lifecycle management
```

**Deliverables:**

- `POST /v1/audio/transcriptions` — Upload file to S3, create job in PostgreSQL, publish event
- `GET /v1/audio/transcriptions/{job_id}` — Return job status and transcript if complete

---

### 1.3: Engine SDK Skeleton

```text
dalston/engine_sdk/
├── __init__.py
├── base.py                    # Abstract Engine class
├── runner.py                  # Queue polling loop
├── io.py                      # Task I/O helpers (S3 download/upload)
└── types.py                   # TaskInput, TaskOutput dataclasses
```

**Deliverables:**

- `Engine` abstract base class with `process(input: TaskInput) -> TaskOutput`
- `EngineRunner` that polls Redis queue and calls `process()`
- `TaskInput` dataclass: task_id, job_id, audio_path, previous_outputs, config
- `TaskOutput` dataclass: data dict with structured result

---

### 1.4: Stub Transcription Engine

```text
engines/transcribe/stub-transcriber/
├── Dockerfile
├── requirements.txt
├── engine.yaml
└── engine.py
```

**Deliverables:**

- Engine that returns hardcoded transcript: "This is a stub transcript. The system works!"
- Dockerfile with engine SDK installed
- `engine.yaml` declaring stage=transcribe, engine_id=stub-transcriber

---

### 1.5: Stub Merger Engine

```text
engines/merge/stub-merger/
├── Dockerfile
├── requirements.txt
├── engine.yaml
└── engine.py
```

**Deliverables:**

- Engine that passes through transcript from previous task output
- Adds empty speakers array and pipeline metadata

---

### 1.6: Orchestrator Skeleton

```text
dalston/orchestrator/
├── __init__.py
├── main.py                    # Entry point, event loop
├── dag.py                     # Build task DAG
├── scheduler.py               # Push tasks to queues
└── handlers.py                # Event handlers
```

**Deliverables:**

- Subscribe to `dalston:events` Redis pub/sub
- `handle_job_created`: Build DAG (stub: always transcribe → merge), save tasks, queue first task
- `handle_task_completed`: Queue dependent tasks, mark job complete when all done
- `handle_task_failed`: Retry or fail job

---

### 1.7: Docker Compose (Minimal)

**Services:**

| Service | Purpose |
| --- | --- |
| `postgres` | PostgreSQL 16 for job/task persistence |
| `redis` | Redis 7 for queues and pub/sub |
| `gateway` | FastAPI REST API |
| `orchestrator` | Job→task expansion and scheduling |
| `engine-stub-transcriber` | Stub transcription engine |
| `engine-stub-merger` | Stub merger engine |

**Environment variables needed:**

- `DATABASE_URL` — PostgreSQL connection string
- `REDIS_URL` — Redis connection string
- `S3_BUCKET`, `S3_REGION` — S3 artifact storage
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` — AWS credentials

---

## Verification

```bash
# Start all services
docker compose up -d

# Submit job
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@test.wav"
# → {"id": "job_abc123", "status": "pending"}

# Poll (after ~2 seconds)
curl http://localhost:8000/v1/audio/transcriptions/job_abc123
# → {"id": "job_abc123", "status": "completed", "text": "This is a stub transcript..."}
```

---

## Checkpoint

- [ ] **Gateway** accepts file upload, returns job ID
- [ ] **Orchestrator** expands job to tasks, schedules them
- [ ] **Engine SDK** polls queue, processes task, publishes completion
- [ ] **Stub engines** return hardcoded responses
- [ ] **Full flow** works end-to-end

**Next**: [M2: Real Transcription](M02-real-transcription.md) — Replace stubs with faster-whisper

---

## Implementation Notes

### Completed (Task 1.7)

**Files created:**

| File | Purpose |
|------|---------|
| `docker/Dockerfile.gateway` | Gateway container image |
| `docker/Dockerfile.orchestrator` | Orchestrator container image |
| `scripts/verify-m01.sh` | End-to-end verification script |

**docker-compose.yml additions:**

| Service | Notes |
|---------|-------|
| `gateway` | Depends on postgres, redis, minio-init; healthcheck via `/health` |
| `orchestrator` | Depends on postgres, redis |
| `minio-init` | Short-lived container that creates the S3 bucket before gateway starts |

**Key implementation decisions:**

1. **MinIO instead of S3**: For local development, we use MinIO as an S3-compatible object store. The `S3_ENDPOINT_URL` environment variable allows switching between MinIO (local) and real S3 (production).

2. **Pre-built engine base image**: Engine containers extend `dalston/engine-base:latest` which must be built manually before running `docker compose up`:

   ```bash
   docker build -f docker/Dockerfile.engine-base -t dalston/engine-base:latest .
   ```

3. **Database initialization**: Both gateway and orchestrator call `init_db()` on startup, which:
   - Creates all tables via `Base.metadata.create_all`
   - Creates a default tenant (UUID `00000000-...`)

   This approach is acceptable for M01 but has limitations (see below).

### Notes for Future Milestones

#### Database Migrations

The current `init_db()` approach has limitations:

- **No schema evolution**: `create_all` only creates missing tables; it won't modify existing tables if the model changes
- **Race condition**: If gateway and orchestrator start simultaneously, both may try to create the default tenant
- **No rollback**: Cannot undo schema changes

**Recommended for M2+**: Introduce Alembic migrations:

```text
dalston/db/
├── migrations/
│   ├── versions/
│   │   └── 001_initial.py
│   └── env.py
└── alembic.ini
```

Add a migration container or entrypoint script:

```yaml
gateway:
  entrypoint: ["sh", "-c", "alembic upgrade head && uvicorn ..."]
```

#### Engine Base Image CI

Currently the engine base image is built manually. Consider:

- Building it in CI and pushing to a registry
- Using Docker Compose `build.target` for multi-stage builds
- Using a Makefile target: `make build-engine-base`

#### Health Checks for Orchestrator

The orchestrator currently has no health check. For production, consider:

- Adding a `/health` HTTP endpoint (requires adding a small HTTP server)
- Using a Redis PING as a proxy for health
- Writing a heartbeat key to Redis that can be checked

#### Verification Script Improvements

The `scripts/verify-m01.sh` script could be extended to:

- Check all service health before submitting
- Verify Redis queue depths after job completion
- Support CI mode with non-zero exit on any warning
