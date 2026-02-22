# Job Cancellation

## Strategic

### Goal

Allow users to cancel transcription jobs that are no longer needed, saving compute resources and providing better control over the processing pipeline.

### Scope

This spec covers **soft cancellation** (Option A): marking a job as cancelled and preventing future stages from being queued. The current running stage completes naturally.

**Out of scope** (future work):

- Hard cancellation: interrupting currently running engine tasks
- Cleanup: deleting intermediate artifacts from S3
- Timeout-based force-kill option

### User Stories

1. As an API user, I want to cancel a job I submitted by mistake before it consumes resources
2. As an API user, I want to cancel a long-running job when I no longer need the results
3. As a CLI user, I want to cancel jobs from the command line
4. As a console user, I want to cancel jobs from the web UI
5. As a webhook consumer, I want to be notified when cancellation is complete

---

## Design Rationale

### Why Soft Cancellation?

We evaluated three cancellation strategies:

| Strategy | Description | Complexity | Responsiveness |
|----------|-------------|------------|----------------|
| **Soft Cancel** | Let running tasks finish, cancel queued | Low | Seconds to minutes |
| **Hard Cancel** | Kill running processes immediately | High | Immediate |
| **Checkpoint Cancel** | Abort at next processing checkpoint | Medium | Depends on checkpoint frequency |

**Decision: Soft Cancel** for these reasons:

1. **ML inference is hard to interrupt** - Whisper runs a single forward pass per audio chunk with no natural interruption points. Killing mid-inference risks corrupted GPU state (leaked CUDA contexts).

2. **Industry standard** - AWS Transcribe, Google Speech-to-Text, AssemblyAI, and Deepgram all use soft cancellation. Running jobs complete; queued jobs are cancelled.

3. **Pipeline is short** - With stages `PREPARE → TRANSCRIBE → ALIGN → MERGE`, usually only one task runs at a time. Cancellation prevents the *remaining* pipeline from executing.

4. **Engine simplicity** - Engines remain stateless with no bidirectional communication. No engine SDK changes required.

5. **Predictable behavior** - A task either completes fully or never starts. No partial outputs or corrupted state.

### Two-Phase Status Model

To accurately represent cancellation state, we use two statuses:

| Status | Meaning |
|--------|---------|
| `CANCELLING` | Cancel requested, one or more tasks may still be running |
| `CANCELLED` | Fully cancelled, no work in progress |

This distinction is important for user experience:

- `CANCELLING` tells users "we heard you, waiting for in-flight work to complete"
- `CANCELLED` confirms the job has fully stopped

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

### Task-Level Behavior

When a job is cancelled, tasks are handled based on their current state:

| Task State | Action |
|------------|--------|
| `PENDING` | Mark as `CANCELLED`, never queued |
| `READY` | Mark as `CANCELLED`; engine skips stream message via cancellation check |
| `RUNNING` | Let complete naturally (task → COMPLETED/FAILED) |
| `COMPLETED` | No action (already done) |
| `FAILED` | No action (already terminal) |

**Key invariant**: Once a job is in `CANCELLING` state, no new tasks are queued for that job.

---

## Tactical

### API Design

**Endpoint:** `POST /v1/audio/transcriptions/{job_id}/cancel`

Uses POST for action semantics, reserving DELETE for job deletion (removing job record + artifacts).

**Request:** No body required

**Response (success):**

```json
{
  "id": "job_abc123",
  "status": "cancelling",
  "message": "Cancellation requested. 1 task still running."
}
```

Or if nothing was running:

```json
{
  "id": "job_abc123",
  "status": "cancelled",
  "message": "Job cancelled."
}
```

**Error responses:**

| Status | Condition | Response |
|--------|-----------|----------|
| 404 | Job not found or wrong tenant | `{"error": {"code": "job_not_found", "message": "Job not found"}}` |
| 409 | Job already completed | `{"error": {"code": "invalid_state", "message": "Job already completed"}}` |
| 409 | Job already failed | `{"error": {"code": "invalid_state", "message": "Job already failed"}}` |
| 409 | Job already cancelled/cancelling | `{"error": {"code": "already_cancelled", "message": "Job already cancelled"}}` |

### Gateway Implementation

The POST cancel endpoint in `dalston/gateway/api/v1/transcription.py`:

1. Fetch job by ID with tenant isolation
2. Validate job is in cancellable state (PENDING or RUNNING)
3. Update all PENDING tasks to CANCELLED
4. Update job status to CANCELLING (or CANCELLED if no running tasks)
5. Publish `job.cancel_requested` event to Redis
6. Return response with new status

```python
@router.post("/{job_id}/cancel")
async def cancel_job(
    job_id: UUID,
    tenant: Tenant = Depends(get_tenant),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> JobCancelledResponse:
    """Cancel a pending or running transcription job."""
    job = await jobs_service.get_job(db, job_id, tenant.id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status in (JobStatus.COMPLETED.value, JobStatus.FAILED.value):
        raise HTTPException(status_code=409, detail=f"Job already {job.status}")

    if job.status in (JobStatus.CANCELLED.value, JobStatus.CANCELLING.value):
        raise HTTPException(status_code=409, detail="Job already cancelled")

    # Cancel pending tasks and check for running tasks
    result = await jobs_service.cancel_job(db, job)

    # Publish event for orchestrator
    await publish_job_cancel_requested(redis, job_id)

    return JobCancelledResponse(
        id=job.id,
        status=result.status,
        message=result.message,
    )
```

### Orchestrator Behavior

**Event handler** for `job.cancel_requested`:

```python
async def handle_job_cancel_requested(
    job_id: UUID,
    db: AsyncSession,
    redis: Redis,
) -> None:
    """Handle job.cancel_requested event.

    1. Mark job as cancelled in Redis (engines check this before processing)
    2. Fetch all tasks for the job
    3. For READY/PENDING tasks: mark CANCELLED
    4. If no RUNNING tasks: transition job to CANCELLED
    """
    await mark_job_cancelled(redis, str(job_id))

    result = await db.execute(
        select(TaskModel).where(TaskModel.job_id == job_id)
    )
    tasks = list(result.scalars().all())

    for task in tasks:
        if task.status in (TaskStatus.PENDING.value, TaskStatus.READY.value):
            # With Streams, tasks are not removed from Redis.
            # Engines skip them after reading due to cancellation flag.
            task.status = TaskStatus.CANCELLED.value

    await db.commit()

    # Check if we can finalize cancellation
    await _check_job_cancellation_complete(job_id, db, redis)
```

**Modified check points** (prevent queuing new tasks):

1. **In `handle_job_created()`**: Before queuing initial tasks, check if job status is CANCELLING
2. **In `handle_task_completed()`**: Before queuing dependent tasks, re-fetch job and check status

```python
# In handle_task_completed, before queuing dependents:
job = await db.get(JobModel, job_id)
if job.status == JobStatus.CANCELLING.value:
    # Don't queue new tasks, mark dependents as cancelled
    for dependent in dependents_to_queue:
        dependent.status = TaskStatus.CANCELLED.value
    await db.commit()
    await _check_job_cancellation_complete(job_id, db, redis)
    return
```

**Transition to CANCELLED:**

```python
async def _check_job_cancellation_complete(
    job_id: UUID,
    db: AsyncSession,
    redis: Redis,
) -> None:
    """Check if cancellation is complete and finalize."""
    job = await db.get(JobModel, job_id)
    if job.status != JobStatus.CANCELLING.value:
        return

    result = await db.execute(
        select(TaskModel).where(TaskModel.job_id == job_id)
    )
    tasks = list(result.scalars().all())

    # Check if any tasks are still running
    running = [t for t in tasks if t.status == TaskStatus.RUNNING.value]

    if not running:
        job.status = JobStatus.CANCELLED.value
        job.completed_at = datetime.now(UTC)
        await db.commit()
        await publish_job_cancelled(redis, job_id)
```

### Webhook Events

Add a new webhook event type for cancellation, following the same pattern as `transcription.completed` and `transcription.failed`:

**Event type:** `transcription.cancelled`

**Payload:**

```json
{
  "event": "transcription.cancelled",
  "transcription_id": "job_abc123",
  "status": "cancelled",
  "timestamp": "2026-02-10T12:00:00Z",
  "webhook_metadata": {"user_id": "123"}
}
```

**Why notify on cancellation?**

1. **Async workflows need confirmation** - The user initiates cancel (→ `CANCELLING`), but there may be a delay before it's fully cancelled (waiting for running task). Webhooks confirm when processing has truly stopped.

2. **Consistency with other terminal states** - We already notify on `completed` and `failed`. All terminal states should trigger webhooks for consistent lifecycle tracking.

3. **Audit/compliance** - Organizations may need proof that processing was stopped (e.g., for GDPR requests).

### Admin Webhook Event Types (M21 Integration)

The admin webhook system (M21) allows registering endpoints with specific event subscriptions. Add `transcription.cancelled` as a subscribable event:

**Updated allowed event types:**

| Event | Description |
|-------|------------|
| `transcription.completed` | Batch job finished successfully |
| `transcription.failed` | Batch job failed permanently |
| `transcription.cancelled` | Batch job was cancelled by user |
| `*` | Wildcard — all events |

**Registration example:**

```json
POST /v1/webhooks
{
  "url": "https://my-server.com/hooks/dalston",
  "events": ["transcription.completed", "transcription.failed", "transcription.cancelled"],
  "description": "Full lifecycle tracking"
}
```

This allows users to subscribe only to events they care about (e.g., some may only want `completed`, others want full lifecycle visibility including cancellations).

**Implementation:** In `dalston/orchestrator/main.py`, when handling `job.cancelled` event:

```python
async def handle_job_cancelled(job_id: UUID, db: AsyncSession, redis: Redis):
    """Deliver webhook for cancelled job."""
    job = await db.get(JobModel, job_id)
    if job and job.webhook_url:
        await webhook_service.deliver(
            url=job.webhook_url,
            event="transcription.cancelled",
            transcription_id=str(job_id),
            status="cancelled",
            webhook_metadata=job.webhook_metadata,
        )
```

### SDK & CLI

**SDK** (`sdk/dalston_sdk/client.py`):

```python
def cancel(self, job_id: str) -> Job:
    """Cancel a pending or running job.

    Args:
        job_id: The job ID to cancel

    Returns:
        Job with updated status (cancelling or cancelled)

    Raises:
        DalstonError: If job cannot be cancelled (already terminal)
    """
    response = self._request("POST", f"/v1/audio/transcriptions/{job_id}/cancel")
    return Job.model_validate(response)
```

**CLI** (`cli/dalston_cli/commands/jobs.py`):

```python
@jobs_app.command("cancel")
def cancel_job(
    job_id: Annotated[str, typer.Argument(help="Job ID to cancel")],
    json_output: bool = Option(False, "--json", help="Output as JSON"),
) -> None:
    """Cancel a pending or running job."""
    client = get_client()
    try:
        job = client.cancel(job_id)
        if json_output:
            console.print_json(job.model_dump_json())
        else:
            console.print(f"Job {job_id} status: {job.status}")
    except DalstonError as e:
        console.print(f"[red]Error:[/red] {e.message}")
        raise typer.Exit(1)
```

### Console (Web UI)

**Console API** (`dalston/gateway/api/console.py`):

```python
@router.post("/jobs/{job_id}/cancel")
async def cancel_job(
    job_id: UUID,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> JobCancelledResponse:
    """Cancel a pending or running job."""
    # Similar to main API, without tenant isolation for admin console
```

**Frontend changes:**

1. **StatusBadge** (`web/src/components/StatusBadge.tsx`):
   - Add `cancelling` style (amber/orange to indicate in-progress cancellation)

   ```typescript
   cancelling: 'bg-amber-500/20 text-amber-400',
   cancelled: 'bg-zinc-500/20 text-zinc-400',  // already exists
   ```

2. **Status filters** (`web/src/pages/BatchJobs.tsx`):
   - Add 'Cancelled' to STATUS_FILTERS array
   - Optionally add 'Cancelling' for debugging/admin purposes

3. **Action buttons** (`web/src/pages/BatchJobs.tsx`):
   - Cancel and Delete are **mutually exclusive** based on job state:
     - **Cancel button** (X icon): shown for `PENDING` or `RUNNING` jobs
     - **Delete button** (Trash icon): shown for terminal states (`COMPLETED`, `FAILED`, `CANCELLED`)
   - Cancel requires no confirmation (user can resubmit if needed)
   - On cancel click: call API, show toast "Cancellation requested", invalidate query

4. **API client** (`web/src/api/client.ts`):
   - Add `cancelJob(jobId: string)` method

   ```typescript
   async cancelJob(jobId: string): Promise<JobCancelledResponse> {
     const response = await fetch(`${this.baseUrl}/console/jobs/${jobId}/cancel`, {
       method: 'POST',
     })
     if (!response.ok) throw new Error('Failed to cancel job')
     return response.json()
   }
   ```

5. **Job types** (`web/src/api/types.ts`):
   - Add `cancelling` to `JobStatus` type

   ```typescript
   export type JobStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled' | 'cancelling'
   ```

---

## Edge Cases

| Scenario | Expected Behavior |
|----------|-------------------|
| Cancel PENDING job | Immediate → CANCELLED (no tasks were running) |
| Cancel RUNNING job (1 task active) | → CANCELLING → (task completes) → CANCELLED |
| Cancel already CANCELLING job | 409 "Job already cancelled" (idempotent-ish) |
| Cancel COMPLETED/FAILED/CANCELLED | 409 "Job already {status}" |
| Task fails while CANCELLING | Job still → CANCELLED (not FAILED) |
| Task completes while CANCELLING | Mark dependents CANCELLED, don't queue them |
| All tasks CANCELLED before merge | Merge never runs (marked CANCELLED) |
| Race: cancel during job creation | Check job status before queuing initial tasks |

---

## Plan

### Files to Modify

| File | Change |
|------|--------|
| `dalston/common/models.py` | Add `CANCELLING` to JobStatus enum |
| `dalston/common/events.py` | Add `publish_job_cancel_requested()` and `publish_job_cancelled()` |
| `sdk/dalston_sdk/types.py` | Add `CANCELLING` to JobStatus enum |
| `dalston/gateway/models/responses.py` | Add `JobCancelledResponse` model |
| `dalston/gateway/services/jobs.py` | Add `cancel_job()` method |
| `dalston/gateway/api/v1/transcription.py` | Add POST cancel endpoint |
| `dalston/gateway/api/console.py` | Add POST /jobs/{job_id}/cancel endpoint |
| `dalston/orchestrator/handlers.py` | Add `handle_job_cancel_requested()`, modify task completion handlers |
| `dalston/orchestrator/main.py` | Subscribe to `job.cancel_requested` and `job.cancelled` events |
| `cli/dalston_cli/commands/jobs.py` | Add cancel command |
| `sdk/dalston_sdk/client.py` | Add `cancel()` method |
| `web/src/api/client.ts` | Add `cancelJob()` method |
| `web/src/pages/BatchJobs.tsx` | Add cancel button for PENDING/RUNNING jobs |

### Implementation Tasks

#### Phase 1: Core Backend (Day 1)

- [ ] Add `CANCELLING = "cancelling"` to `JobStatus` enum in `dalston/common/models.py`
- [ ] Add `CANCELLING = "cancelling"` to `JobStatus` enum in `sdk/dalston_sdk/types.py`
- [ ] Add `publish_job_cancel_requested()` event function in `dalston/common/events.py`
- [ ] Add `publish_job_cancelled()` event function in `dalston/common/events.py`
- [ ] Add `JobCancelledResponse` model in `dalston/gateway/models/responses.py`
- [ ] Add `cancel_job()` method to `JobsService` in `dalston/gateway/services/jobs.py`
- [ ] Add `POST /{job_id}/cancel` endpoint in `dalston/gateway/api/v1/transcription.py`

#### Phase 2: Orchestrator (Day 2)

- [ ] Add `handle_job_cancel_requested()` handler in `dalston/orchestrator/handlers.py`
- [ ] Add `_check_job_cancellation_complete()` helper function
- [ ] Add cancellation check in `handle_job_created()` before initial task queuing
- [ ] Add cancellation check in `handle_task_completed()` before dependent task queuing
- [ ] Subscribe to `job.cancel_requested` event in `dalston/orchestrator/main.py`
- [ ] Ensure READY stream messages are skipped via cancellation flag checks

#### Phase 3: Webhook Delivery (Day 2-3)

- [ ] Add `handle_job_cancelled()` handler for webhook delivery
- [ ] Subscribe to `job.cancelled` event in `dalston/orchestrator/main.py`
- [ ] Deliver `transcription.cancelled` webhook with same retry logic as other events

#### Phase 4: SDK & CLI (Day 3)

- [ ] Add `cancel()` method to `Dalston` client in `sdk/dalston_sdk/client.py`
- [ ] Add `cancel()` method to `AsyncDalston` client
- [ ] Add `cancel` command in `cli/dalston_cli/commands/jobs.py`

#### Phase 5: Console UI (Day 3-4)

- [ ] Add `POST /jobs/{job_id}/cancel` endpoint in `dalston/gateway/api/console.py`
- [ ] Add `cancelJob()` to web API client in `web/src/api/client.ts`
- [ ] Add cancel button to BatchJobs page for PENDING/RUNNING jobs
- [ ] Add toast notifications for cancel success/error

#### Phase 6: Testing (Day 4)

- [ ] Add unit tests for cancel endpoint (all state transitions)
- [ ] Add unit tests for orchestrator cancel handlers
- [ ] Add unit test for webhook delivery on cancellation
- [ ] Add integration test for mid-pipeline cancellation
- [ ] Add E2E test: submit job, wait for RUNNING, cancel, verify CANCELLED + webhook

### Verification

1. **Unit test**: Cancel a PENDING job → status becomes CANCELLED immediately
2. **Unit test**: Cancel a RUNNING job → status becomes CANCELLING, then CANCELLED after task completes
3. **Unit test**: Cancel already completed job → 409 error
4. **Integration test**:
   - Submit job with diarization (multi-stage pipeline)
   - Wait for RUNNING status (transcription in progress)
   - Cancel the job
   - Verify current task completes but no new tasks are queued
   - Verify final status is CANCELLED
   - Verify webhook delivered with `transcription.cancelled` event
5. **CLI test**: Run `dalston jobs cancel <job_id>` and verify response
6. **Console test**: Click cancel button on RUNNING job, verify status updates to CANCELLING
7. **Error cases**: Attempt to cancel completed/failed jobs, verify 409 response

---

## Future Enhancements

1. **Timeout-based force-cancel**: `POST /cancel?timeout=30` - if running tasks don't complete in N seconds, restart engine container
2. **Partial results on cancel**: Return whatever transcript was generated before cancellation
3. **S3 artifact cleanup**: Delete intermediate artifacts when job is cancelled (save storage)
4. **Bulk cancellation**: Cancel multiple jobs in one API call
