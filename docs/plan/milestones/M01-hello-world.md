# M1: "Hello World" Transcription

| | |
|---|---|
| **Goal** | Prove the complete batch flow works end-to-end with stubs |
| **Duration** | 2-3 days |
| **Dependencies** | None (first milestone) |
| **Deliverable** | Submit file → get stub transcript back |

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
