# Job Deletion

## Strategic

### Goal

Allow users to permanently delete completed transcription jobs and their associated data, supporting data retention policies, storage management, and GDPR compliance.

### Scope

This spec covers **permanent deletion** of jobs that have reached a terminal state. Deletion removes both the database record and all associated artifacts from storage.

**In scope:**

- Delete job record from database
- Delete audio file from S3
- Delete transcript and intermediate artifacts from S3

**Out of scope:**

- Soft delete / archival (jobs are permanently removed)
- Bulk deletion API
- Automatic retention-based deletion (TTL)

### User Stories

1. As an API user, I want to delete old jobs to free up storage and comply with data retention policies
2. As a GDPR data subject, I want my audio recordings deleted when I request it
3. As a CLI user, I want to clean up test jobs from the command line
4. As a console user, I want to delete jobs from the web UI

### Constraints

Only jobs in **terminal states** can be deleted:

| Status | Deletable | Reason |
|--------|-----------|--------|
| `PENDING` | No | Job hasn't started, use cancel instead |
| `RUNNING` | No | Job in progress, use cancel instead |
| `CANCELLING` | No | Cancellation in progress, wait for completion |
| `COMPLETED` | Yes | Terminal state |
| `FAILED` | Yes | Terminal state |
| `CANCELLED` | Yes | Terminal state |

---

## Tactical

### API Design

**Endpoint:** `DELETE /v1/audio/transcriptions/{job_id}`

**Request:** No body required

**Response (success):** `204 No Content`

**Error responses:**

| Status | Condition | Response |
|--------|-----------|----------|
| 404 | Job not found or wrong tenant | `{"error": {"code": "job_not_found", "message": "Job not found"}}` |
| 400 | Job is PENDING | `{"error": {"code": "invalid_state", "message": "Cannot delete pending job. Use cancel instead."}}` |
| 400 | Job is RUNNING | `{"error": {"code": "invalid_state", "message": "Cannot delete running job. Use cancel instead."}}` |
| 400 | Job is CANCELLING | `{"error": {"code": "invalid_state", "message": "Cannot delete job while cancellation is in progress."}}` |

### Gateway Implementation

The DELETE endpoint in `dalston/gateway/api/v1/transcription.py`:

1. Fetch job by ID with tenant isolation
2. Validate job is in deletable state (COMPLETED, FAILED, or CANCELLED)
3. Delete all S3 artifacts via `StorageService.delete_job_artifacts()`
4. Delete job record from database (cascades to tasks)
5. Return 204 No Content

### Storage Cleanup

The `StorageService.delete_job_artifacts()` method already exists and deletes:

- `jobs/{job_id}/input/` — Original audio file
- `jobs/{job_id}/tasks/` — Intermediate task outputs
- `jobs/{job_id}/output/` — Final transcript

### Database Cascade

The `TaskModel` has `CASCADE` delete on the job foreign key, so deleting the job automatically deletes all associated tasks.

### SDK & CLI

**SDK** (`sdk/dalston_sdk/client.py`): Add `delete()` method

```python
def delete(self, job_id: str) -> None:
    """Delete a completed job and its artifacts."""
    self._request("DELETE", f"/v1/audio/transcriptions/{job_id}")
```

**CLI** (`cli/dalston_cli/commands/jobs.py`): Add delete command

```python
@app.command("delete")
def delete_job(job_id: str) -> None:
    """Delete a completed job and its artifacts."""
```

### Console (Web UI)

**Console API** (`dalston/gateway/api/console.py`): Add delete endpoint

```python
@router.delete("/jobs/{job_id}")
async def delete_job(job_id: UUID) -> Response:
    """Delete a job and its artifacts."""
```

**Frontend** (`web/src/pages/BatchJobs.tsx`): Add delete button

- Show delete button/icon for jobs in terminal states (COMPLETED, FAILED, CANCELLED)
- Confirmation dialog before deletion: "Are you sure? This will permanently delete the job and its transcript."
- On success, remove job from list and show toast notification
- On error, show error message

---

## Plan

### Files to Modify

| File | Change |
|------|--------|
| `dalston/gateway/api/v1/transcription.py` | Add DELETE endpoint |
| `dalston/gateway/api/console.py` | Add DELETE /jobs/{job_id} endpoint |
| `dalston/gateway/services/jobs.py` | Add `delete_job()` method |
| `sdk/dalston_sdk/client.py` | Add `delete()` method |
| `cli/dalston_cli/commands/jobs.py` | Add delete command |
| `web/src/api/client.ts` | Add `deleteJob()` method |
| `web/src/pages/BatchJobs.tsx` | Add delete button with confirmation |

### Implementation Tasks

- [ ] Add `delete_job()` method to `JobsService` in `dalston/gateway/services/jobs.py`
- [ ] Add `DELETE /{job_id}` endpoint in `dalston/gateway/api/v1/transcription.py`
- [ ] Add `DELETE /jobs/{job_id}` endpoint in `dalston/gateway/api/console.py`
- [ ] Add `delete()` method to SDK client
- [ ] Add `delete` command to CLI
- [ ] Add `deleteJob()` to web API client
- [ ] Add delete button to BatchJobs page with confirmation dialog
- [ ] Add unit tests for delete endpoint
- [ ] Add integration test for full deletion flow

### Verification

1. **Unit test**: Delete a COMPLETED job → returns 204, job gone from database
2. **Integration test**:
   - Submit and complete a job
   - Verify artifacts exist in S3
   - Delete the job
   - Verify job record gone from database
   - Verify artifacts gone from S3
3. **CLI test**: `dalston jobs delete <job_id>`
4. **Console test**: Click delete button, confirm, verify job removed from list
5. **Error cases**:
   - Delete non-existent job → 404
   - Delete RUNNING job → 400
   - Delete PENDING job → 400
   - Delete job from different tenant → 404
