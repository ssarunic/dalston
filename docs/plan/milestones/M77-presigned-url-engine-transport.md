# M77: Presigned URL Engine Transport

|                    |                                                                                              |
| ------------------ | -------------------------------------------------------------------------------------------- |
| **Goal**           | Engines fetch inputs and store outputs via plain HTTP using presigned URLs — no S3 credentials, no boto3 |
| **Duration**       | 4–6 days                                                                                     |
| **Dependencies**   | M33 (Reliable Task Queues), M16 (AWS Deployment)                                             |
| **Deliverable**    | Credential-free engine containers, presigned URL generation service, HTTP transport in runner |
| **Status**         | Not Started                                                                                  |

## User Story

> *"As a platform operator, I want to add a new transcription engine without distributing S3 credentials to it. The engine should be able to fetch its input and store its output using only information included in the task message — so I can run engines on untrusted compute or give engine authors access without giving them bucket access."*

---

## Outcomes

| Scenario | Current | After M77 |
| -------- | ------- | --------- |
| Engine container environment | `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` in every engine | No credentials in any engine container |
| Engine dependencies | `boto3`/`botocore` required in every engine image | `httpx` only (already present) |
| Storage backend coupling | Engines configure endpoint URL for MinIO vs S3 | Engines do plain HTTP — no storage awareness |
| Adding a new engine | Must distribute credentials and configure S3 SDK | Engine author implements `process()` only |
| Running engines on third-party compute | Credential leakage risk | Presigned URLs expire; blast radius is one task |
| Local dev vs cloud parity | `DALSTON_S3_ENDPOINT_URL` varies per environment | Same engine image, same code path everywhere |

---

## Design

### Presigned URL as the bridge

The orchestrator holds S3 credentials and generates two presigned URLs per task at dispatch time:

- A **presigned GET** for `jobs/{job_id}/tasks/{task_id}/request.json` — generated immediately after writing the file
- A **presigned PUT** for `jobs/{job_id}/tasks/{task_id}/response.json` — pre-generated before writing `request.json`, embedded inside it

Both URLs are also written to the Redis task metadata hash (`dalston:task:{task_id}`) for auditability and retry support.

TTL is **7 days**. This is intentionally generous: the real security boundary is "no permanent credentials in engines", which presigned URLs already enforce. Short TTLs add operational complexity (expiry-induced retry failures) without meaningfully narrowing the attack surface for internal cluster traffic.

### What does not change

- The orchestrator retains S3 credentials and continues to write `request.json` and read `response.json` directly via boto3
- `previous_responses` in `TaskRequestData` is unchanged — the orchestrator still assembles it from dependency stage outputs before writing `request.json`
- Completion signaling is unchanged — engines continue to publish `task.completed` and `task.failed` to the Redis event stream
- Real-time workers are out of scope — they never touch S3

```
┌────────────────────────────────────────────────────────────────────────────────┐
│                        PRESIGNED URL TASK FLOW                                  │
│                                                                                 │
│   Orchestrator (has S3 creds)         Redis hash             Engine (no creds) │
│                                                                                 │
│   1. build request.json                                                           │
│      (previous_responses, config)                                                 │
│                                                                                 │
│   2. write request.json ──────────────▶ S3                                       │
│                                                                                 │
│   3. generate_get_url(request.json) ──▶ dalston:task:{id}                        │
│      generate_put_url(response.json) ─▶  input_json_url: https://...             │
│                                        output_url:     https://...             │
│                                                                                 │
│   4. XADD task to stream ───────────────────────────────────▶ XREADGROUP       │
│                                                                                 │
│   5.                                                          HGET input_json_url
│                                                               GET  https://...  │
│                                                               ◀── request.json   │
│                                                                                 │
│   6.                                                          engine.process()  │
│                                                                                 │
│   7.                                                          PUT  output_url   │
│                                                               ──▶ response.json  │
│                                                                                 │
│   8.                                                          XADD task.completed
│                                                                                 │
│   9. HGET / S3 GET response.json ◀────────────────────────────────               │
│      advance DAG                                                                │
│                                                                                 │
└────────────────────────────────────────────────────────────────────────────────┘
```

---

## Steps

### 77.1: Presigned URL Generation Service

**Files modified:**

- `dalston/common/presigned.py` *(new)*

**Deliverables:**

```python
def generate_get_url(s3_uri: str, ttl_seconds: int = 604800) -> str:
    """Generate a presigned GET URL for an existing S3 object."""

def generate_put_url(s3_uri: str, ttl_seconds: int = 604800) -> str:
    """Generate a presigned PUT URL for a not-yet-existing S3 object."""
```

Both functions:

- Accept `s3://bucket/key` URI format (consistent with the rest of the codebase)
- Build a boto3 client using `DALSTON_S3_ENDPOINT_URL` (MinIO), `DALSTON_S3_REGION`, and standard `AWS_*` credentials from settings
- Pass `endpoint_url` to `generate_presigned_url` when set — required for MinIO URLs to resolve correctly inside the Docker network

Unit tests:

- Verify MinIO endpoint injection produces a URL with the correct host
- Verify URL contains expected bucket and key path
- Verify PUT URL is accepted by MinIO (round-trip test against local stack)

No runtime changes in this step.

---

### 77.2: Orchestrator — Embed Presigned URLs at Dispatch

**Files modified:**

- `dalston/orchestrator/scheduler.py`
- `dalston/common/types.py` (TaskRequestData)
- `dalston/db/models.py`
- `alembic/versions/` *(new migration)*

**Changes to `TaskRequestData`:**

Add one field:

```python
@dataclass
class TaskRequestData:
    # ... existing fields ...
    output_url: str  # Presigned PUT for engine to store result
```

**Changes to `scheduler.py:queue_task()`:**

After step 5 (write `request.json` to S3), add:

1. Generate presigned GET URL for the `request.json` just written: `input_json_url`
2. Generate presigned PUT URL for `response.json` (path: `jobs/{job_id}/tasks/{task_id}/response.json`): `output_url`
3. Re-write `request.json` with `output_url` embedded in the payload (or generate `output_url` before writing and include it in the first write — preferred, avoids a second S3 PUT)
4. Write both URLs to the Redis task metadata hash:

```python
await redis.hset(f"dalston:task:{task_id}", mapping={
    # ... existing fields ...
    "input_json_url": input_json_url,
    "output_url": output_url,
})
```

**Database migration:**

Add two nullable columns to the `tasks` table:

```sql
ALTER TABLE tasks ADD COLUMN input_json_url TEXT;
ALTER TABLE tasks ADD COLUMN output_url TEXT;
```

Populate on write, not backfilled. Existing rows remain NULL and continue to work on the old code path until M77.3 is deployed.

No engine or SDK changes in this step. Verify URLs are generated and stored correctly before proceeding.

---

### 77.3: Engine SDK — HTTP Transport

**Files modified:**

- `dalston/engine_sdk/http.py` *(new)*
- `dalston/engine_sdk/runner.py`

**New `dalston/engine_sdk/http.py`:**

```python
def fetch_json(url: str) -> dict:
    """HTTP GET a JSON resource. Retries on 5xx with exponential backoff."""

def put_json(url: str, data: dict) -> None:
    """HTTP PUT a JSON payload. Retries on 5xx with exponential backoff."""
```

Uses `httpx` (already present). Raises `EngineTransportError` on non-2xx after retries so the runner can classify transport failures distinctly from engine failures.

**Changes to `runner.py`:**

Replace S3 download at task start:

```python
# Before
request_uri = build_task_request_uri(settings.s3_bucket, job_id, task_id)
task_input_data = io.download_json(request_uri)

# After
input_json_url = redis.hget(f"dalston:task:{task_id}", "input_json_url")
task_input_data = http.fetch_json(input_json_url)
```

Replace S3 upload at task completion:

```python
# Before
response_uri = build_task_response_uri(settings.s3_bucket, job_id, task_id)
io.upload_json(output_data, response_uri)

# After
http.put_json(task_input.output_url, output_data)
```

`dalston/engine_sdk/io.py` is not modified — the orchestrator continues using it directly with its own credentials. Remove `io` imports from `runner.py` only.

---

### 77.4: Credential Removal from Engine Containers

**Files modified:**

- `docker-compose.yml`
- `docker/Dockerfile.engine-base`
- Engine `requirements.txt` files (audit all under `engines/`)

**`docker-compose.yml`:**

Move `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `DALSTON_S3_ENDPOINT_URL` out of `x-common-env`. Add them explicitly to only the `orchestrator` and `gateway` service definitions.

Before:

```yaml
x-common-env: &common-env
  AWS_ACCESS_KEY_ID: ${MINIO_ROOT_USER:-minioadmin}
  AWS_SECRET_ACCESS_KEY: ${MINIO_ROOT_PASSWORD:-minioadmin}
  DALSTON_S3_ENDPOINT_URL: http://minio:9000
  # ... other vars
```

After:

```yaml
x-common-env: &common-env
  # AWS_* and DALSTON_S3_ENDPOINT_URL removed

x-storage-env: &storage-env
  AWS_ACCESS_KEY_ID: ${MINIO_ROOT_USER:-minioadmin}
  AWS_SECRET_ACCESS_KEY: ${MINIO_ROOT_PASSWORD:-minioadmin}
  DALSTON_S3_ENDPOINT_URL: http://minio:9000

orchestrator:
  environment:
    <<: [*common-env, *storage-env, *observability-env]

gateway:
  environment:
    <<: [*common-env, *storage-env, *observability-env]
```

**`docker/Dockerfile.engine-base`:**

Remove `boto3` and `botocore` from the base image install. After this change, any engine that directly imports boto3 will fail at import time — which is the desired behaviour. It makes accidental credential use loudly detectable.

**Engine `requirements.txt` audit:**

Scan all `engines/**/requirements.txt` for `boto3`/`botocore`. Remove. If any engine legitimately uses S3 for something other than task I/O (none should), flag as a follow-up rather than silently removing.

---

### 77.5: Validation

**End-to-end test (`make test-e2e`):**

Submit a job through the full pipeline (prepare → transcribe → align → merge). Assert job completes with a correct transcript. This is the primary regression guard.

**Negative test:**

Spawn a batch engine container explicitly without `AWS_ACCESS_KEY_ID`. Verify it processes a task to completion. Proves no S3 SDK path is reachable from engines.

**URL expiry smoke test:**

Generate a presigned GET URL with a 5-second TTL. Wait 6 seconds. Assert the response is 403. Ensures expiry behaviour is working before any production incident reveals it.

---

## Deployment

M77.2 and M77.3 must be deployed together in a single coordinated rollout. A half-deployed state — orchestrator writing `input_json_url` to the task hash but engine still using the S3 path, or engine reading `input_json_url` before the orchestrator writes it — will silently drop tasks.

Procedure:

1. Stop all services (`make stop` or `make aws-stop`)
2. Deploy orchestrator and all engine images together
3. Restart (`make dev` or `make aws-start`)

M77.4 (credential removal) is safe to follow in the next deploy once M77.2+77.3 are stable.

---

## Non-Goals

- **Real-time workers** — WebSocket engines never touch S3 for task I/O. Out of scope.
- **Presigned URLs for audio uploads from clients** — Gateway-to-client presigned URLs are a separate feature (reduces gateway bandwidth). Out of scope.
- **URL refresh on expiry** — 7-day TTL makes this unnecessary. If a job is still queued after 7 days, something is already very wrong.
- **Multipart upload** — Engine outputs are JSON, always well under 5 GB. Single-part PUT is sufficient.

---

## Verification

```bash
# Start stack
make dev

# Submit a job and verify it completes end-to-end
export DALSTON_API_KEY=$(grep DALSTON_API_KEY .env | cut -d= -f2)
JOB_ID=$(curl -s -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  -F "file=@tests/fixtures/short.wav" | jq -r '.id')

# Poll until done
watch -n 2 "curl -s http://localhost:8000/v1/audio/transcriptions/$JOB_ID \
  -H 'Authorization: Bearer $DALSTON_API_KEY' | jq '{status, text}'"

# Verify no AWS credentials in engine containers
docker compose exec stt-transcribe-faster-whisper-base \
  env | grep -E "AWS_|S3_ENDPOINT" || echo "PASS: no credentials in engine"

# Verify presigned URLs are written to task metadata
TASK_ID=$(docker compose exec redis redis-cli KEYS "dalston:task:*" | head -1 | tr -d '\r')
docker compose exec redis redis-cli HGETALL "$TASK_ID" | grep -A1 "input_json_url"

# URL expiry smoke test
python3 - <<'EOF'
import time
from dalston.common.presigned import generate_get_url
import httpx

url = generate_get_url("s3://dalston-artifacts/test-expiry.txt", ttl_seconds=5)
time.sleep(6)
r = httpx.get(url)
assert r.status_code == 403, f"Expected 403, got {r.status_code}"
print("PASS: expired URL correctly returns 403")
EOF
```

---

## Checkpoint

- [ ] `dalston/common/presigned.py` implemented and unit tested
- [ ] `TaskRequestData` gains `output_url` field
- [ ] Orchestrator generates and stores both presigned URLs at dispatch time
- [ ] `input_json_url` and `output_url` written to Redis task metadata hash
- [ ] DB migration adds nullable `input_json_url`, `output_url` columns to `tasks`
- [ ] `dalston/engine_sdk/http.py` implemented with retry and `EngineTransportError`
- [ ] `runner.py` uses HTTP GET for input, HTTP PUT for output
- [ ] `runner.py` no longer imports `dalston.engine_sdk.io`
- [ ] `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` absent from all engine container envs
- [ ] `boto3` removed from `Dockerfile.engine-base` and engine `requirements.txt` files
- [ ] End-to-end test passes with no credentials in engine containers
- [ ] Negative test confirms engines work without `AWS_*` env vars
- [ ] Expiry smoke test confirms 403 on expired URLs
