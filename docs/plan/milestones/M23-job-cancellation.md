# M23: Job Cancellation

|               |                                                                             |
| ------------- | --------------------------------------------------------------------------- |
| **Goal**      | Allow users to cancel pending or running transcription jobs                 |
| **Duration**  | 3-4 days                                                                    |
| **Dependencies** | None (core feature, unblocks M12/M13)                                    |
| **Deliverable** | Cancel API endpoint, orchestrator support, SDK/CLI/Console integration    |
| **Status**    | Completed                                                                   |

## User Story

> *"As a user, I want to cancel a transcription job I no longer need, so I don't waste compute resources and can manage my pipeline effectively."*

---

## Overview

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                       CANCELLATION FLOW                                      │
│                                                                              │
│  User Request                                                                │
│       │                                                                      │
│       ▼                                                                      │
│  POST /v1/audio/transcriptions/{job_id}/cancel                               │
│       │                                                                      │
│       ▼                                                                      │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │  Gateway                                                                │ │
│  │  1. Validate job is cancellable (PENDING or RUNNING)                    │ │
│  │  2. Mark PENDING tasks as CANCELLED                                     │ │
│  │  3. Set job status to CANCELLING (or CANCELLED if nothing running)      │ │
│  │  4. Publish job.cancel_requested event                                  │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│       │                                                                      │
│       ▼                                                                      │
│  ┌─────────────────────────────────────────────────────────────────────────┐ │
│  │  Orchestrator                                                           │ │
│  │  1. Remove READY tasks from Redis queues (LREM)                         │ │
│  │  2. Let RUNNING tasks complete naturally                                │ │
│  │  3. Don't queue any new dependent tasks                                 │ │
│  │  4. When all tasks terminal → job.status = CANCELLED                    │ │
│  │  5. Deliver transcription.cancelled webhook                             │ │
│  └─────────────────────────────────────────────────────────────────────────┘ │
│                                                                              │
│  State Transitions:                                                          │
│                                                                              │
│    PENDING ────────────────────────────────────► CANCELLED (immediate)       │
│                                                                              │
│    RUNNING ──► CANCELLING ──(task completes)──► CANCELLED + webhook          │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Design Decision: Soft Cancellation

We use **soft cancellation**: running tasks complete naturally, only queued/pending work is cancelled.

**Why not hard cancellation (kill running tasks)?**

1. ML inference (Whisper) has no graceful interruption points
2. Killing mid-inference risks GPU state corruption (CUDA context leaks)
3. Industry standard (AWS, Google, AssemblyAI all use soft cancel)
4. Keeps engines stateless and simple

See [JOB_CANCELLATION.md](../../specs/batch/JOB_CANCELLATION.md) for full design rationale.

---

## Steps

### 23.1: Job Status Enum Update

**Deliverables:**

- Add `CANCELLING = "cancelling"` to `JobStatus` enum in `dalston/common/models.py`
- Add `CANCELLING = "cancelling"` to `JobStatus` enum in `sdk/dalston_sdk/types.py`

---

### 23.2: Event Functions

**Deliverables:**

- Add `publish_job_cancel_requested(redis, job_id)` in `dalston/common/events.py`
- Add `publish_job_cancelled(redis, job_id)` in `dalston/common/events.py`

---

### 23.3: Gateway Cancel Endpoint

**Deliverables:**

- Add `JobCancelledResponse` model in `dalston/gateway/models/responses.py`
- Add `cancel_job()` method to `JobsService` in `dalston/gateway/services/jobs.py`
- Add `POST /{job_id}/cancel` endpoint in `dalston/gateway/api/v1/transcription.py`

**Endpoint behavior:**

```
POST /v1/audio/transcriptions/{job_id}/cancel

Response (200):
{
  "id": "job_abc123",
  "status": "cancelling",  // or "cancelled" if nothing was running
  "message": "Cancellation requested. 1 task still running."
}

Errors:
- 404: Job not found
- 409: Job already completed/failed/cancelled
```

---

### 23.4: Orchestrator Cancel Handler

**Deliverables:**

- Add `handle_job_cancel_requested()` handler in `dalston/orchestrator/handlers.py`
- Add `_check_job_cancellation_complete()` helper function
- Subscribe to `job.cancel_requested` event in `dalston/orchestrator/main.py`
- Add Redis queue removal logic (LREM) for READY tasks
- Add cancellation check in `handle_job_created()` before initial task queuing
- Add cancellation check in `handle_task_completed()` before dependent task queuing

---

### 23.5: Webhook Delivery

**Deliverables:**

- Add `handle_job_cancelled()` handler for webhook delivery
- Subscribe to `job.cancelled` event in orchestrator main loop
- Deliver `transcription.cancelled` webhook with same retry logic as other events

**Webhook payload:**

```json
{
  "event": "transcription.cancelled",
  "transcription_id": "job_abc123",
  "status": "cancelled",
  "timestamp": "2026-02-10T12:00:00Z",
  "webhook_metadata": {"user_id": "123"}
}
```

---

### 23.6: SDK Integration

**Deliverables:**

- Add `cancel()` method to `Dalston` client in `sdk/dalston_sdk/client.py`
- Add `cancel()` method to `AsyncDalston` client

```python
def cancel(self, job_id: str) -> Job:
    """Cancel a pending or running job."""
    response = self._request("POST", f"/v1/audio/transcriptions/{job_id}/cancel")
    return Job.model_validate(response)
```

---

### 23.7: CLI Integration

**Deliverables:**

- Add `cancel` command in `cli/dalston_cli/commands/jobs.py`

```bash
dalston jobs cancel <job_id>
dalston jobs cancel <job_id> --json
```

---

### 23.8: Console Integration

**Deliverables:**

**Backend:**

- Add `POST /jobs/{job_id}/cancel` endpoint in `dalston/gateway/api/console.py`

**Frontend:**

- Add `cancelling` to `JobStatus` type in `web/src/api/types.ts`
- Add `cancelJob()` method to API client in `web/src/api/client.ts`
- Add `cancelling` style to StatusBadge (amber/orange for in-progress cancellation)
- Add 'Cancelled' filter to status filters in BatchJobs page
- Add Cancel button (X icon) for PENDING/RUNNING jobs (mutually exclusive with Delete button which is for terminal states)
- Show toast notification: "Cancellation requested" on success

---

### 23.9: Tests

**Deliverables:**

- Unit tests for cancel endpoint (all state transitions)
- Unit tests for orchestrator cancel handlers
- Unit test for webhook delivery on cancellation
- Integration test for mid-pipeline cancellation
- E2E test: submit job → wait for RUNNING → cancel → verify CANCELLED + webhook

---

## Verification

```bash
# Test cancel via API
JOB_ID=$(curl -s -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@long_audio.mp3" | jq -r '.id')

# Wait for job to start
sleep 5

# Cancel
curl -X POST http://localhost:8000/v1/audio/transcriptions/$JOB_ID/cancel
# {"id": "...", "status": "cancelling", "message": "Cancellation requested. 1 task still running."}

# Check final status
curl http://localhost:8000/v1/audio/transcriptions/$JOB_ID
# {"status": "cancelled", ...}

# Test via CLI
dalston jobs cancel $JOB_ID

# Test error cases
curl -X POST http://localhost:8000/v1/audio/transcriptions/$JOB_ID/cancel
# 409: {"error": {"code": "already_cancelled", "message": "Job already cancelled"}}
```

---

## Checkpoint

- [x] `CANCELLING` status added to JobStatus enums (backend + SDK + web types)
- [x] Event functions for cancel_requested and cancelled
- [x] Gateway cancel endpoint with proper error handling
- [x] Orchestrator handler removes tasks from queues
- [x] Cancellation checks prevent queuing new tasks
- [x] Job transitions to CANCELLED when all tasks terminal
- [x] `transcription.cancelled` webhook delivered
- [x] `transcription.cancelled` added to M21 allowed event types
- [x] SDK `cancel()` method
- [x] CLI `dalston jobs cancel` command
- [x] Console API `POST /jobs/{job_id}/cancel` endpoint
- [x] Console StatusBadge shows `cancelling` (amber) and `cancelled` (orange) states
- [x] Console BatchJobs page has Cancel button for PENDING/RUNNING jobs
- [x] Console BatchJobs page has 'Cancelled' status filter
- [x] Unit and integration tests passing

---

## Implementation Notes

### Files Changed

| File | Description |
|------|-------------|
| `dalston/common/models.py` | Added `CANCELLING` to JobStatus, `CANCELLED` to TaskStatus |
| `dalston/common/events.py` | Added `publish_job_cancel_requested()`, `publish_job_cancelled()` |
| `dalston/gateway/api/v1/transcription.py` | Added `POST /{job_id}/cancel` endpoint |
| `dalston/gateway/api/console.py` | Added `POST /jobs/{job_id}/cancel` admin endpoint |
| `dalston/gateway/services/jobs.py` | Added `cancel_job()` service method |
| `dalston/gateway/models/responses.py` | Added `JobCancelledResponse` model |
| `dalston/gateway/services/webhook_endpoints.py` | Added `transcription.cancelled` to allowed events |
| `dalston/orchestrator/handlers.py` | Added `handle_job_cancel_requested()`, `_check_job_cancellation_complete()`, cancellation checks |
| `dalston/orchestrator/main.py` | Subscribed to `job.cancel_requested` event |
| `dalston/orchestrator/scheduler.py` | Added `remove_task_from_queue()` for Redis LREM |
| `sdk/dalston_sdk/types.py` | Added `CANCELLING` to JobStatus, `TRANSCRIPTION_CANCELLED` to WebhookEvent |
| `sdk/dalston_sdk/client.py` | Added `cancel()` method to sync and async clients |
| `cli/dalston_cli/commands/jobs.py` | Added `cancel` command |
| `web/src/api/types.ts` | Added `cancelling` to JobStatus type |
| `web/src/api/client.ts` | Added `cancelJob()` method |
| `web/src/components/StatusBadge.tsx` | Added `cancelling` (amber) and updated `cancelled` (orange) styles |
| `web/src/pages/BatchJobs.tsx` | Added Cancel button, 'Cancelled' filter, cancel dialog |

### Test Coverage

- `tests/integration/test_cancel_job_api.py` — Integration tests for cancel endpoint
- `tests/e2e/test_job_cancellation_e2e.py` — E2E tests for full cancellation flow
- `tests/unit/test_jobs_service.py` — Unit tests for cancel_job service method

---

## Unblocked

This milestone unblocked M12 (Python SDK) and M13 (CLI), which now include:

- **SDK**: `client.cancel(job_id)` method (sync and async)
- **CLI**: `dalston jobs cancel <job_id>` command

---

## Future Enhancements

1. **Timeout-based force-cancel**: `POST /cancel?timeout=30` - restart engine container if task doesn't complete
2. **Partial results**: Return transcript generated before cancellation
3. **S3 cleanup**: Delete intermediate artifacts to save storage
4. **Bulk cancellation**: Cancel multiple jobs in one API call
