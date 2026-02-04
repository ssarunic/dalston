# Job Cancellation

## Strategic

### Goal

Allow users to cancel transcription jobs that are no longer needed, saving compute resources and providing better control over the processing pipeline.

### Scope

This spec covers **soft cancellation** (Option 1): marking a job as cancelled and preventing future stages from being queued. The current running stage completes naturally.

**Out of scope** (future work):

- Hard cancellation: interrupting currently running engine tasks
- Cleanup: deleting intermediate artifacts from S3
- Webhook notification for cancellation events

### User Stories

1. As an API user, I want to cancel a job I submitted by mistake before it consumes resources
2. As an API user, I want to cancel a long-running job when I no longer need the results
3. As a CLI user, I want to cancel jobs from the command line
4. As a console user, I want to cancel jobs from the web UI

### Status Model

Two-phase cancellation for accurate state representation:

| Status | Meaning |
|--------|---------|
| `CANCELLING` | Cancel requested, current task may still be running |
| `CANCELLED` | Fully cancelled, no work in progress |

**Transition diagram:**

```
PENDING ──────────────────────────────────→ CANCELLING → CANCELLED
    │                                            ↑
    ↓                                            │
RUNNING ──────────────────────────────────────────┘
    │
    ↓
COMPLETED / FAILED
```

---

## Tactical

### API Design

**Endpoint:** `POST /v1/audio/transcriptions/{job_id}/cancel`

Uses POST for action semantics, reserving DELETE for future job deletion (removing job record + artifacts).

**Request:** No body required

**Response (success):**

```json
{
  "id": "job_abc123",
  "status": "cancelling"
}
```

**Error responses:**

| Status | Condition | Response |
|--------|-----------|----------|
| 404 | Job not found or wrong tenant | `{"error": {"code": "job_not_found", "message": "Job not found"}}` |
| 400 | Job already completed | `{"error": {"code": "invalid_state", "message": "Job already completed"}}` |
| 400 | Job already failed | `{"error": {"code": "invalid_state", "message": "Job already failed"}}` |
| 400 | Job already cancelled/cancelling | `{"error": {"code": "invalid_state", "message": "Job already cancelled"}}` |

### Gateway Implementation

The POST cancel endpoint in `dalston/gateway/api/v1/transcription.py`:

1. Fetch job by ID with tenant isolation
2. Validate job is in cancellable state (PENDING or RUNNING)
3. Update status to CANCELLING
4. Return response with new status

### Orchestrator Behavior

**Check points** (prevent queuing new tasks):

1. **In `handle_job_created()`**: Before queuing initial tasks, check if job status is CANCELLING
2. **In `handle_task_completed()`**: Before queuing dependent tasks, re-fetch job and check status

**Transition to CANCELLED:**

In `_check_job_completion()`, add logic:

- If job status is CANCELLING AND no tasks are RUNNING → transition to CANCELLED

This naturally handles the completion of any in-flight work before finalizing cancellation.

### SDK & CLI

**SDK** (`sdk/dalston_sdk/client.py`): Update `cancel()` method to call POST endpoint

**CLI** (`cli/dalston_cli/commands/jobs.py`): Uncomment existing cancel command (lines 172-178)

### Console (Web UI)

**Console API** (`dalston/gateway/api/console.py`): Add cancel endpoint

```python
@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: UUID) -> JobCancelledResponse:
    """Cancel a pending or running job."""
```

**Frontend** (`web/src/pages/BatchJobs.tsx`): Add cancel button

- Show cancel button/icon for jobs in cancellable states (PENDING, RUNNING)
- On click, call cancel API immediately (no confirmation needed - cancellation is reversible by resubmitting)
- Update job status in list to CANCELLING
- Show toast notification on success/error

---

## Plan

### Files to Modify

| File | Change |
|------|--------|
| `dalston/common/models.py` | Add `CANCELLING` to JobStatus enum |
| `sdk/dalston_sdk/types.py` | Add `CANCELLING` to JobStatus enum |
| `dalston/gateway/models/responses.py` | Add `JobCancelledResponse` model |
| `dalston/gateway/api/v1/transcription.py` | Add POST cancel endpoint |
| `dalston/gateway/api/console.py` | Add POST /jobs/{job_id}/cancel endpoint |
| `dalston/orchestrator/handlers.py` | Add cancellation checks + CANCELLED transition |
| `cli/dalston_cli/commands/jobs.py` | Uncomment cancel command |
| `sdk/dalston_sdk/client.py` | Update cancel() to use POST endpoint |
| `web/src/api/client.ts` | Add `cancelJob()` method |
| `web/src/pages/BatchJobs.tsx` | Add cancel button for PENDING/RUNNING jobs |

### Implementation Tasks

- [ ] Add `CANCELLING = "cancelling"` to `JobStatus` enum in `dalston/common/models.py`
- [ ] Add `CANCELLING = "cancelling"` to `JobStatus` enum in `sdk/dalston_sdk/types.py`
- [ ] Add `JobCancelledResponse` model in `dalston/gateway/models/responses.py`
- [ ] Add `POST /{job_id}/cancel` endpoint in `dalston/gateway/api/v1/transcription.py`
- [ ] Add `POST /jobs/{job_id}/cancel` endpoint in `dalston/gateway/api/console.py`
- [ ] Add cancellation check in `handle_job_created()` before initial task queuing
- [ ] Add cancellation check in `handle_task_completed()` before dependent task queuing
- [ ] Add CANCELLING → CANCELLED transition in `_check_job_completion()`
- [ ] Update `cancel()` method in SDK client to use POST endpoint
- [ ] Uncomment and update cancel command in CLI
- [ ] Add `cancelJob()` to web API client
- [ ] Add cancel button to BatchJobs page
- [ ] Add unit tests for cancel endpoint
- [ ] Add integration test for mid-pipeline cancellation

### Verification

1. **Unit test**: Cancel a PENDING job → status becomes CANCELLING immediately, then CANCELLED
2. **Integration test**:
   - Submit job with diarization (multi-stage pipeline)
   - Wait for RUNNING status
   - Cancel the job
   - Verify current task completes but no new tasks are queued
   - Verify final status is CANCELLED
3. **CLI test**: Run `dalston jobs cancel <job_id>` and verify response
4. **Console test**: Click cancel button on RUNNING job, verify status updates to CANCELLING
5. **Error cases**: Attempt to cancel completed/failed jobs, verify 400 response
