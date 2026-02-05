# Data Retention & Cleanup

## Strategic

### Goal

Provide configurable, automated lifecycle management for audio files, transcripts, and intermediate artifacts — enabling privacy compliance (GDPR, HIPAA, CCPA), bounded storage costs, and operator control over data residency.

### Scope

This spec covers the full data retention system: per-job retention policies, system/tenant defaults, the background cleanup worker, selective deletion (audio-only vs. full), and the delete-on-demand API extensions.

**In scope:**

- Per-job retention mode and duration
- System-wide and per-tenant default configuration
- Background cleanup worker for expired jobs
- Selective deletion (audio only, full artifacts, or transcript only)
- Delete-on-demand API endpoints
- Real-time session artifact cleanup
- Integration with existing [Job Deletion](batch/JOB_DELETION.md) spec

**Out of scope:**

- Audit logging (see [AUDIT_LOG.md](AUDIT_LOG.md))
- Crypto-shredding / per-job encryption (future enterprise feature)
- Legal holds (future feature)
- Bulk erasure API (future GDPR "right to be forgotten" endpoint)
- Backup-aware deletion (depends on crypto-shredding)

**Related documents:**

- [ADR-004: Data Retention Strategy](../decisions/ADR-004-data-retention-strategy.md) — Why this approach was chosen
- [Data Model](batch/DATA_MODEL.md) — Database schemas this spec extends
- [Job Deletion](batch/JOB_DELETION.md) — Existing manual deletion behavior
- [Architecture](ARCHITECTURE.md) — System overview

### User Stories

1. As an **operator**, I want to set a default retention period so storage costs don't grow unbounded
2. As an **operator**, I want to cap the maximum retention period so tenants can't keep data forever on my infrastructure
3. As a **tenant admin**, I want to set a default retention policy for my organization that overrides the system default
4. As an **API user**, I want to specify retention per job (e.g., "delete after 1 hour" or "keep indefinitely")
5. As an **API user**, I want to delete just the source audio but keep the transcript
6. As a **privacy-sensitive user**, I want zero-retention mode where audio is deleted immediately after transcription completes
7. As a **GDPR data subject**, I want my data deleted on request, with confirmation
8. As an **operator**, I want to see what data was purged and when, without needing to check S3 directly

### Industry Context

This design is informed by how major transcription providers handle retention:

| Pattern | Providers | Dalston Equivalent |
|---------|-----------|-------------------|
| Zero retention for streaming | Deepgram, Google, Azure | `retention_mode: none` |
| Short TTL with auto-delete | AssemblyAI (3d BAA), Rev.ai (30d) | `retention_mode: auto_delete` |
| Configurable per-request TTL | Azure (`timeToLive`), ElevenLabs | `retention_hours` parameter |
| Customer-controlled (keep) | AWS Transcribe | `retention_mode: keep` |
| Delete-on-demand API | AssemblyAI, Rev.ai | `DELETE /v1/audio/transcriptions/{id}` |

---

## Tactical

### Retention Modes

Each job has a `retention_mode` that controls its lifecycle:

| Mode | Behavior | Use Case |
|------|----------|----------|
| `auto_delete` | Artifacts purged after `retention_hours` | Default. Most common for production workloads |
| `keep` | Nothing auto-deleted; user manages lifecycle via DELETE API | Development, debugging, long-term archival |
| `none` | Artifacts deleted immediately when job reaches terminal state | Maximum privacy. HIPAA, sensitive recordings |

### Retention Scope

Not all artifacts carry the same sensitivity. The `retention_scope` field controls what gets deleted:

| Scope | What Gets Deleted | What Survives |
|-------|-------------------|---------------|
| `all` (default) | Audio + intermediate artifacts + transcript + exports | Job metadata row (id, status, timestamps, parameters) |
| `audio_only` | Audio + intermediate artifacts | Transcript, exports, job metadata |

The job row in PostgreSQL is **never** deleted by the retention worker. It is marked as `purged` and retained for billing, audit, and historical reference. Only a manual `DELETE /v1/audio/transcriptions/{id}` removes the row itself (per [Job Deletion](batch/JOB_DELETION.md)).

### Configuration Hierarchy

Retention settings follow a layered hierarchy. Each layer can override the one above it, within the bounds set by the operator:

```
System defaults (env vars)
  └─ Tenant overrides (tenant.settings JSONB)
      └─ Per-job overrides (submission parameters)
```

#### System Defaults (Environment Variables)

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `RETENTION_DEFAULT_MODE` | string | `auto_delete` | Default retention mode |
| `RETENTION_DEFAULT_HOURS` | integer | `24` | Default hours before auto-delete |
| `RETENTION_DEFAULT_SCOPE` | string | `all` | Default deletion scope |
| `RETENTION_MAX_HOURS` | integer | `720` (30 days) | Maximum retention any tenant/job can request |
| `RETENTION_MIN_HOURS` | integer | `0` | Minimum retention (0 = immediate) |
| `RETENTION_CLEANUP_INTERVAL_SECONDS` | integer | `300` (5 min) | How often the cleanup worker runs |

#### Tenant Overrides

Stored in the `tenants.settings` JSONB column:

```json
{
  "retention": {
    "default_mode": "auto_delete",
    "default_hours": 48,
    "default_scope": "audio_only",
    "max_hours": 168
  }
}
```

Tenant `max_hours` cannot exceed the system `RETENTION_MAX_HOURS`. The effective maximum for a tenant is `min(tenant.max_hours, system.RETENTION_MAX_HOURS)`.

#### Per-Job Overrides (Submission Parameters)

Jobs submitted via `POST /v1/audio/transcriptions` or `POST /v1/speech-to-text` can include retention parameters:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `retention_mode` | string | tenant/system default | `auto_delete`, `keep`, or `none` |
| `retention_hours` | integer | tenant/system default | Hours until auto-delete (only for `auto_delete` mode) |
| `retention_scope` | string | tenant/system default | `all` or `audio_only` |

**Validation rules:**

- `retention_hours` must be between `RETENTION_MIN_HOURS` and the effective max for the tenant
- `retention_hours` is ignored when `retention_mode` is `keep` or `none`
- If `retention_mode` is `none`, `retention_scope` defaults to `all` (zero retention deletes everything)

### Data Model Changes

#### Jobs Table — New Columns

```sql
ALTER TABLE jobs ADD COLUMN retention_mode VARCHAR(20) NOT NULL DEFAULT 'auto_delete';
ALTER TABLE jobs ADD COLUMN retention_hours INTEGER NOT NULL DEFAULT 24;
ALTER TABLE jobs ADD COLUMN retention_scope VARCHAR(20) NOT NULL DEFAULT 'all';
ALTER TABLE jobs ADD COLUMN purged_at TIMESTAMPTZ;
ALTER TABLE jobs ADD COLUMN purge_after TIMESTAMPTZ;

CREATE INDEX idx_jobs_purge_after ON jobs(purge_after)
    WHERE purge_after IS NOT NULL AND purged_at IS NULL;
```

| Column | Type | Description |
|--------|------|-------------|
| `retention_mode` | VARCHAR(20) | `auto_delete`, `keep`, `none` |
| `retention_hours` | INTEGER | Hours after completion before purge |
| `retention_scope` | VARCHAR(20) | `all` or `audio_only` |
| `purged_at` | TIMESTAMPTZ | When artifacts were actually deleted (null = not purged) |
| `purge_after` | TIMESTAMPTZ | Computed: `completed_at + retention_hours`. Used by cleanup worker index |

The `purge_after` column is computed at job completion time:

- `auto_delete`: `purge_after = completed_at + interval '{retention_hours} hours'`
- `none`: `purge_after = completed_at` (immediate)
- `keep`: `purge_after = NULL` (never auto-purge)

#### Job Status After Purge

A purged job retains its original terminal status (`completed`, `failed`, `cancelled`). The `purged_at` timestamp indicates artifacts have been removed. API responses include this field:

```json
{
  "id": "job_abc123",
  "status": "completed",
  "purged_at": "2025-02-01T14:00:00Z",
  "retention": {
    "mode": "auto_delete",
    "hours": 24,
    "scope": "all"
  }
}
```

Attempting to download a transcript or audio for a purged job returns:

```json
{
  "error": {
    "code": "artifacts_purged",
    "message": "Job artifacts were purged at 2025-02-01T14:00:00Z per retention policy.",
    "purged_at": "2025-02-01T14:00:00Z"
  }
}
```

### API Design

#### Submission — Retention Parameters

**Dalston Native** (`POST /v1/audio/transcriptions`):

```bash
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer dk_xxx" \
  -F "file=@meeting.mp3" \
  -F "retention_mode=auto_delete" \
  -F "retention_hours=48" \
  -F "retention_scope=audio_only"
```

**ElevenLabs Compatible** (`POST /v1/speech-to-text`):

Retention parameters are accepted as additional form fields. They are Dalston extensions and not part of the ElevenLabs spec.

#### Delete Audio Only

**Endpoint:** `DELETE /v1/audio/transcriptions/{job_id}/audio`

Deletes the source audio and intermediate artifacts but preserves the transcript and exports. This is a common pattern: the audio is sensitive, the transcript is useful.

**Request:** No body required

**Response (success):** `204 No Content`

**Error responses:**

| Status | Condition | Response |
|--------|-----------|----------|
| 404 | Job not found or wrong tenant | `{"error": {"code": "job_not_found", "message": "Job not found"}}` |
| 400 | Job not in terminal state | `{"error": {"code": "invalid_state", "message": "Cannot delete artifacts for a running job."}}` |
| 410 | Audio already purged | `{"error": {"code": "already_purged", "message": "Audio artifacts already purged."}}` |

**Side effects:**

- Sets `purged_at` if not already set
- Updates `retention_scope` to reflect what was deleted
- Emits `audio.deleted` audit event

#### Get Retention Info

The existing `GET /v1/audio/transcriptions/{id}` response is extended with a `retention` block:

```json
{
  "id": "job_abc123",
  "status": "completed",
  "retention": {
    "mode": "auto_delete",
    "hours": 24,
    "scope": "all",
    "purge_after": "2025-02-02T12:00:00Z",
    "purged_at": null
  }
}
```

#### List Jobs — Filter by Retention State

The existing `GET /v1/audio/transcriptions` endpoint gains optional query parameters:

| Parameter | Type | Description |
|-----------|------|-------------|
| `purged` | boolean | Filter by purge state (`true` = purged, `false` = not purged) |
| `retention_mode` | string | Filter by retention mode |

#### Tenant Retention Settings

**Endpoint:** `PATCH /v1/tenants/{tenant_id}/settings`

```json
{
  "retention": {
    "default_mode": "auto_delete",
    "default_hours": 48,
    "default_scope": "audio_only",
    "max_hours": 168
  }
}
```

Requires `admin` scope. Validation ensures tenant `max_hours` does not exceed system `RETENTION_MAX_HOURS`.

### Cleanup Worker

The cleanup worker is a background process that periodically purges expired jobs. It runs as part of the orchestrator process (not a separate service) to keep deployment simple.

#### Sweep Logic

```
Every RETENTION_CLEANUP_INTERVAL_SECONDS:
  1. Query: SELECT * FROM jobs
       WHERE purge_after IS NOT NULL
         AND purge_after <= NOW()
         AND purged_at IS NULL
       ORDER BY purge_after ASC
       LIMIT 100

  2. For each expired job:
     a. Delete S3 artifacts based on retention_scope:
        - scope "all":        delete jobs/{id}/audio/*, jobs/{id}/tasks/*, jobs/{id}/transcript.json, exports/{id}/*
        - scope "audio_only": delete jobs/{id}/audio/*, jobs/{id}/tasks/*
     b. Delete Redis keys: dalston:task:{task_id}:* for all tasks in job
     c. Set purged_at = NOW() on the job row
     d. Emit audit event: job.purged

  3. Log summary: "Purged {n} expired jobs"
```

#### Idempotency

The cleanup worker is idempotent. If it crashes mid-sweep:

- Jobs where S3 deletion succeeded but `purged_at` wasn't set will be re-processed. S3 delete on non-existent keys is a no-op.
- The `LIMIT 100` prevents unbounded work per sweep cycle.

#### Zero-Retention Mode (`none`)

For jobs with `retention_mode: none`, the orchestrator triggers cleanup inline when the job reaches a terminal state, rather than waiting for the next sweep cycle:

```
On job completion/failure:
  If retention_mode == "none":
    Delete S3 artifacts immediately
    Set purged_at = NOW()
    Emit audit event: job.purged
```

This ensures zero-retention jobs never have artifacts sitting in S3 between sweep intervals.

### Real-Time Session Cleanup

Real-time WebSocket sessions (`/v1/audio/transcriptions/stream`) have a different data flow. Audio streams through memory and is not persisted by default.

| Artifact | Default Behavior | With `save_session: true` |
|----------|------------------|---------------------------|
| Streaming audio | Not persisted (memory only) | Saved to `sessions/{id}/audio.wav` |
| Partial transcripts | Not persisted | Saved to `sessions/{id}/partials/*.json` |
| Final transcript | Not persisted | Saved to `sessions/{id}/final.json` |

When `save_session: true` is set, the session artifacts follow the same retention policy as batch jobs. A `retention_mode` and `retention_hours` can be passed as WebSocket connection parameters.

If no retention parameters are specified for a saved session, the tenant/system defaults apply.

### ElevenLabs API Compatibility

The ElevenLabs API does not have a native retention parameter. Dalston accepts `retention_mode`, `retention_hours`, and `retention_scope` as additional form fields on the `/v1/speech-to-text` endpoint. These are silently ignored by actual ElevenLabs clients and are a Dalston extension.

The ElevenLabs-compatible delete endpoint follows the same pattern:

```
DELETE /v1/speech-to-text/transcripts/{transcription_id}
```

Maps to the same underlying deletion logic as the Dalston native endpoint.

---

## Plan

### Files to Create

| File | Purpose |
|------|---------|
| `dalston/gateway/services/retention.py` | Retention policy resolution (system → tenant → job) |
| `dalston/orchestrator/cleanup.py` | Background cleanup worker |

### Files to Modify

| File | Change |
|------|--------|
| `dalston/common/models.py` | Add `RetentionMode`, `RetentionScope` enums |
| `dalston/gateway/models/responses.py` | Add `RetentionInfo` response model |
| `dalston/gateway/api/v1/transcription.py` | Accept retention params on POST, add DELETE audio endpoint |
| `dalston/gateway/api/v1/elevenlabs.py` | Accept retention params on POST |
| `dalston/gateway/services/jobs.py` | Compute `purge_after` on job completion, integrate retention service |
| `dalston/orchestrator/handlers.py` | Trigger inline purge for `retention_mode: none` on job completion |
| `dalston/orchestrator/main.py` | Start cleanup worker as background task |
| `dalston/gateway/services/storage.py` | Add `delete_job_audio()` method for audio-only deletion |
| `alembic/versions/xxx_add_retention_columns.py` | Migration adding retention columns to jobs table |

### Implementation Tasks

- [ ] Add `RetentionMode` enum (`auto_delete`, `keep`, `none`) to `dalston/common/models.py`
- [ ] Add `RetentionScope` enum (`all`, `audio_only`) to `dalston/common/models.py`
- [ ] Add retention columns to jobs table (Alembic migration)
- [ ] Create `RetentionService` in `dalston/gateway/services/retention.py`
  - [ ] `resolve_policy(tenant, job_params) → RetentionPolicy` — merges system/tenant/job settings
  - [ ] `validate_policy(policy, tenant) → None` — raises if policy exceeds tenant/system bounds
- [ ] Update `POST /v1/audio/transcriptions` to accept and validate retention parameters
- [ ] Update `POST /v1/speech-to-text` to accept retention parameters
- [ ] Compute `purge_after` when job reaches terminal state
- [ ] Create cleanup worker in `dalston/orchestrator/cleanup.py`
  - [ ] Periodic sweep of expired jobs
  - [ ] S3 artifact deletion based on `retention_scope`
  - [ ] Redis key cleanup for task metadata
  - [ ] Set `purged_at` on job row
- [ ] Add inline purge for `retention_mode: none` in orchestrator job completion handler
- [ ] Add `DELETE /v1/audio/transcriptions/{id}/audio` endpoint
- [ ] Add `delete_job_audio()` to `StorageService`
- [ ] Extend `GET /v1/audio/transcriptions/{id}` response with retention info
- [ ] Add `purged` and `retention_mode` query filters to list endpoint
- [ ] Add `PATCH /v1/tenants/{id}/settings` endpoint for tenant retention defaults
- [ ] Handle `artifacts_purged` error when accessing purged job transcripts
- [ ] Add retention parameters to WebSocket connection for `save_session: true`
- [ ] Unit tests for retention policy resolution
- [ ] Unit tests for cleanup worker sweep logic
- [ ] Integration test: submit job with `retention_hours=0`, verify immediate purge
- [ ] Integration test: submit job with `retention_mode=keep`, verify no auto-purge
- [ ] Integration test: `DELETE .../audio` preserves transcript

### Verification

1. **Policy resolution**: System default → tenant override → job override, with max cap enforcement
2. **Auto-delete flow**:
   - Submit job with `retention_mode=auto_delete`, `retention_hours=1`
   - Complete the job
   - Verify `purge_after` is set to `completed_at + 1 hour`
   - Wait for cleanup sweep (or trigger manually)
   - Verify S3 artifacts deleted, `purged_at` set, job row retained
3. **Zero-retention flow**:
   - Submit job with `retention_mode=none`
   - Complete the job
   - Verify artifacts deleted immediately (no waiting for sweep)
4. **Keep mode**:
   - Submit job with `retention_mode=keep`
   - Verify `purge_after` is NULL
   - Verify cleanup worker ignores this job
5. **Audio-only delete**:
   - Submit and complete a job
   - `DELETE /v1/audio/transcriptions/{id}/audio`
   - Verify audio gone from S3, transcript still accessible
6. **Purged job access**:
   - Access transcript of a purged job → `410 Gone` with `artifacts_purged` error
   - Access job metadata of a purged job → `200 OK` with `purged_at` set
7. **Error cases**:
   - `retention_hours` exceeds tenant max → 400
   - `retention_hours` exceeds system max → 400
   - Delete audio on a non-terminal job → 400
   - Delete audio on already-purged job → 410
