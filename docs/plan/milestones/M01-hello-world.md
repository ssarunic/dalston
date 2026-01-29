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

```
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

**Deliverables**:
- `config.py` with Redis URL, data paths via environment variables
- `redis.py` with async connection factory
- Core enums: `JobStatus`, `TaskStatus`
- Core models: `Job`, `Task` as Pydantic models

---

### 1.2: Gateway Skeleton

```
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

**Implementation**:

```python
# transcription.py
@router.post("/v1/audio/transcriptions")
async def create_transcription(file: UploadFile) -> CreateJobResponse:
    job_id = f"job_{uuid4().hex[:12]}"

    # 1. Upload file to S3: s3://{bucket}/jobs/{job_id}/audio/original.*
    # 2. Create Job record in PostgreSQL (status="pending")
    # 3. Publish "job.created" event (Redis pub/sub)

    return {"id": job_id, "status": "pending"}

@router.get("/v1/audio/transcriptions/{job_id}")
async def get_transcription(job_id: str) -> JobResponse:
    # Load job from PostgreSQL, return status + transcript (from S3) if complete
    pass
```

---

### 1.3: Engine SDK Skeleton

```
dalston/engine_sdk/
├── __init__.py
├── base.py                    # Abstract Engine class
├── runner.py                  # Queue polling loop
├── io.py                      # Task I/O helpers
└── types.py                   # TaskInput, TaskOutput dataclasses
```

**Core abstractions**:

```python
# base.py
class Engine(ABC):
    @abstractmethod
    def process(self, input: TaskInput) -> TaskOutput:
        """Override in concrete engines."""
        pass
    
    def run(self):
        """SDK handles queue polling."""
        EngineRunner(self).start()

# types.py
@dataclass
class TaskInput:
    task_id: str
    job_id: str
    audio_path: Path
    previous_outputs: dict[str, Any]  # Results from dependency tasks
    config: dict[str, Any]            # Engine-specific config

@dataclass  
class TaskOutput:
    data: dict[str, Any]              # Structured result
```

---

### 1.4: Stub Transcription Engine

```
engines/transcribe/stub-transcriber/
├── Dockerfile
├── requirements.txt
├── engine.yaml
└── engine.py
```

```python
# engine.py
class StubTranscriber(Engine):
    def process(self, input: TaskInput) -> TaskOutput:
        return TaskOutput(data={
            "text": "This is a stub transcript. The system works!",
            "segments": [
                {"start": 0.0, "end": 2.0, "text": "This is a stub transcript."},
                {"start": 2.0, "end": 4.0, "text": "The system works!"}
            ],
            "language": "en"
        })

if __name__ == "__main__":
    StubTranscriber().run()
```

---

### 1.5: Stub Merger Engine

```
engines/merge/stub-merger/
├── Dockerfile
├── requirements.txt  
├── engine.yaml
└── engine.py
```

```python
class StubMerger(Engine):
    def process(self, input: TaskInput) -> TaskOutput:
        transcribe_output = input.previous_outputs.get("transcribe", {})
        return TaskOutput(data={
            "text": transcribe_output.get("text", ""),
            "segments": transcribe_output.get("segments", []),
            "speakers": [],
            "metadata": {"pipeline": ["stub-transcriber", "stub-merger"]}
        })
```

---

### 1.6: Orchestrator Skeleton

```
dalston/orchestrator/
├── __init__.py
├── main.py                    # Entry point, event loop
├── dag.py                     # Build task DAG (stub: always 2 tasks)
├── scheduler.py               # Push tasks to queues
└── handlers.py                # Event handlers
```

**Stub DAG builder** (always returns transcribe → merge):

```python
# dag.py
def build_task_dag(job: Job) -> list[Task]:
    """For M1, always return: transcribe → merge"""
    transcribe = Task(
        id=f"task_{uuid4().hex[:8]}",
        job_id=job.id,
        stage="transcribe",
        engine_id="stub-transcriber",
        dependencies=[],
        status=TaskStatus.READY
    )
    
    merge = Task(
        id=f"task_{uuid4().hex[:8]}",
        job_id=job.id,
        stage="merge", 
        engine_id="stub-merger",
        dependencies=[transcribe.id],
        status=TaskStatus.PENDING
    )
    
    return [transcribe, merge]
```

**Event handlers**:

```python
# handlers.py
async def handle_job_created(job_id: str):
    job = await load_job(job_id)
    tasks = build_task_dag(job)
    
    for task in tasks:
        await save_task(task)
        await redis.sadd(f"dalston:job:{job_id}:tasks", task.id)
    
    # Queue tasks with no dependencies
    for task in tasks:
        if not task.dependencies:
            await redis.lpush(f"dalston:queue:{task.engine_id}", task.id)
    
    job.status = JobStatus.RUNNING
    await save_job(job)

async def handle_task_completed(task_id: str):
    task = await load_task(task_id)
    job = await load_job(task.job_id)
    all_tasks = await get_job_tasks(job.id)
    
    # Find and queue dependent tasks
    for t in all_tasks:
        if task.id in t.dependencies:
            deps_complete = all(
                (await load_task(d)).status == TaskStatus.COMPLETED 
                for d in t.dependencies
            )
            if deps_complete:
                t.status = TaskStatus.READY
                await save_task(t)
                await redis.lpush(f"dalston:queue:{t.engine_id}", t.id)
    
    # Check if job is complete
    if all(t.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED) for t in all_tasks):
        job.status = JobStatus.COMPLETED
        await save_job(job)
```

---

### 1.7: Docker Compose (Minimal)

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      - POSTGRES_USER=dalston
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
      - POSTGRES_DB=dalston
    ports: ["5432:5432"]
    volumes: [postgres-data:/var/lib/postgresql/data]

  redis:
    image: redis:7-alpine
    command: redis-server --appendonly no
    ports: ["6379:6379"]

  gateway:
    build: { dockerfile: docker/Dockerfile.gateway }
    ports: ["8000:8000"]
    environment:
      - DATABASE_URL=postgresql://dalston:${POSTGRES_PASSWORD}@postgres:5432/dalston
      - REDIS_URL=redis://redis:6379
      - S3_BUCKET=${S3_BUCKET}
      - S3_REGION=${S3_REGION}
      - AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
      - AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}
    depends_on: [postgres, redis]

  orchestrator:
    build: { dockerfile: docker/Dockerfile.orchestrator }
    environment:
      - DATABASE_URL=postgresql://dalston:${POSTGRES_PASSWORD}@postgres:5432/dalston
      - REDIS_URL=redis://redis:6379
      - S3_BUCKET=${S3_BUCKET}
      - S3_REGION=${S3_REGION}
      - AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
      - AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}
    depends_on: [postgres, redis]

  engine-stub-transcriber:
    build: { context: ./engines/transcribe/stub-transcriber }
    environment:
      - REDIS_URL=redis://redis:6379
      - ENGINE_ID=stub-transcriber
      - S3_BUCKET=${S3_BUCKET}
      - S3_REGION=${S3_REGION}
      - AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
      - AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}
    tmpfs: ["/tmp/dalston:size=1G"]
    depends_on: [redis]

  engine-stub-merger:
    build: { context: ./engines/merge/stub-merger }
    environment:
      - REDIS_URL=redis://redis:6379
      - ENGINE_ID=stub-merger
      - S3_BUCKET=${S3_BUCKET}
      - S3_REGION=${S3_REGION}
      - AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
      - AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}
    tmpfs: ["/tmp/dalston:size=1G"]
    depends_on: [redis]

volumes:
  postgres-data:
```

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

✓ **Gateway** accepts file upload, returns job ID  
✓ **Orchestrator** expands job to tasks, schedules them  
✓ **Engine SDK** polls queue, processes task, publishes completion  
✓ **Stub engines** return hardcoded responses  
✓ **Full flow** works end-to-end  

**Next**: [M2: Real Transcription](M02-real-transcription.md) — Replace stubs with faster-whisper
