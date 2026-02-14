# Data Retention & Cleanup

## Strategic

### Goal

Provide configurable, automated lifecycle management for audio files, transcripts, and intermediate artifacts - enabling privacy compliance (GDPR, HIPAA, CCPA), bounded storage costs, and operator control over data residency.

### Scope

This spec covers the full data retention system: named retention policies, per-job policy application, system/tenant defaults, the background cleanup worker, selective deletion, and delete-on-demand API extensions.

**In scope:**

- Retention policies as first-class objects
- Per-job policy assignment (batch, realtime, hybrid)
- System-wide and per-tenant default configuration
- Background cleanup worker for expired jobs
- Selective deletion (audio only vs full artifacts)
- Delete-on-demand API endpoints
- Real-time session artifact cleanup
- ElevenLabs API compatibility

**Out of scope:**

- Audit logging (see [AUDIT_LOG.md](AUDIT_LOG.md))
- Crypto-shredding / per-job encryption (future enterprise feature)
- Legal holds (future feature)
- Bulk erasure API (future GDPR "right to be forgotten" endpoint)
- Backup-aware deletion (depends on crypto-shredding)
- Policy-level access control (future feature)

**Related documents:**

- [ADR-008: Data Retention Strategy](../decisions/ADR-008-data-retention-strategy.md) - Why this approach was chosen
- [Data Model](batch/DATA_MODEL.md) - Database schemas this spec extends
- [Job Cancellation](batch/JOB_CANCELLATION.md) - Cancel vs delete distinction
- [Architecture](ARCHITECTURE.md) - System overview
- [AUDIT_LOG.md](AUDIT_LOG.md) - Audit events for retention operations

### User Stories

1. As an **operator**, I want to set a default retention period so storage costs don't grow unbounded
2. As an **operator**, I want to cap the maximum retention period so tenants can't keep data forever on my infrastructure
3. As a **tenant admin**, I want to define named retention policies (e.g., "hipaa-6yr") for my organization
4. As a **tenant admin**, I want to set which policy is the default for my organization
5. As an **API user**, I want to specify a retention policy per job (e.g., "use zero-retention for this call")
6. As an **API user**, I want to delete just the source audio but keep the transcript
7. As a **privacy-sensitive user**, I want zero-retention mode where audio is deleted immediately after transcription completes
8. As a **GDPR data subject**, I want my data deleted on request, with confirmation
9. As an **operator**, I want to see what data was purged and when, without checking S3 directly

### Industry Context

This design is informed by how major transcription providers handle retention:

| Pattern | Providers | Dalston Equivalent |
|---------|-----------|-------------------|
| Zero retention for streaming | Deepgram, Google, Azure | `zero-retention` policy |
| Short TTL with auto-delete | AssemblyAI (3d BAA), Rev.ai (30d) | `default` policy with custom hours |
| Configurable per-request TTL | Azure (`timeToLive`), ElevenLabs | `retention_policy` parameter |
| Customer-controlled (keep) | AWS Transcribe | `keep` policy |
| Delete-on-demand API | AssemblyAI, Rev.ai | `DELETE /v1/audio/transcriptions/{id}` |

---

## Tactical

### Retention Policies

Retention policies are first-class objects that define how long artifacts are kept and what gets deleted.

#### Policy Fields

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Unique identifier |
| `tenant_id` | UUID | Owning tenant (NULL for system policies) |
| `name` | string | Human-readable name, unique per tenant |
| `mode` | enum | `auto_delete`, `keep`, `none` |
| `hours` | integer | Hours until auto-delete (for `auto_delete` mode) |
| `scope` | enum | `all` or `audio_only` |
| `realtime_mode` | enum | `inherit`, `auto_delete`, `keep`, `none` |
| `realtime_hours` | integer | Hours for realtime (NULL = use `hours`) |
| `delete_realtime_on_enhancement` | boolean | Delete realtime artifacts when enhancement completes |
| `is_system` | boolean | System policies are immutable |
| `created_at` | timestamp | When policy was created |

#### System Policies (Immutable)

| Name | Mode | Hours | Scope | Realtime | Description |
|------|------|-------|-------|----------|-------------|
| `default` | auto_delete | 24 | all | inherit | Standard retention |
| `zero-retention` | none | - | all | inherit | Delete immediately on completion |
| `keep` | keep | - | all | inherit | Never auto-delete |

#### Retention Modes

| Mode | Behavior | Use Case |
|------|----------|----------|
| `auto_delete` | Artifacts purged after `hours` | Default. Most common for production |
| `keep` | Nothing auto-deleted; user manages via DELETE API | Development, debugging, long-term archival |
| `none` | Artifacts deleted immediately when job reaches terminal state | Maximum privacy. HIPAA, sensitive recordings |

#### Retention Scope

| Scope | What Gets Deleted | What Survives |
|-------|-------------------|---------------|
| `all` | Audio + intermediate artifacts + transcript | Job metadata row (id, status, timestamps, parameters) |
| `audio_only` | Audio + intermediate artifacts | Transcript, job metadata |

**Important**: The job row in PostgreSQL is **never** deleted by the retention worker. It is marked with `purged_at` and retained for billing, audit, and historical reference. Only an explicit `DELETE /v1/audio/transcriptions/{id}` removes the row itself.

### Realtime & Hybrid Retention

Retention for realtime sessions depends on whether artifacts are stored:

| Realtime Mode | Artifacts Created | Retention Applies? |
|---------------|-------------------|-------------------|
| `store_audio=false`, `store_transcript=false` | None (memory only) | No |
| `store_audio=true` | Audio in S3 | Yes |
| `store_transcript=true` | Transcript in S3 | Yes |

#### Realtime Policy Settings

Policies have realtime-specific fields:

- `realtime_mode`: If `inherit`, uses same settings as batch. Otherwise explicit mode.
- `realtime_hours`: Override duration for realtime (NULL = use batch `hours`)
- `delete_realtime_on_enhancement`: For hybrid mode, delete realtime artifacts when enhancement job completes

#### Hybrid Mode Flow

When `enhance_on_end=true`:

1. Realtime session stores audio/transcript to S3
2. Enhancement batch job is created on session end
3. If `delete_realtime_on_enhancement=true`: realtime artifacts deleted when enhancement completes
4. Enhancement job follows batch retention settings from the same policy

### Configuration Hierarchy

Retention settings follow a layered hierarchy. Each layer can override the one above it, within bounds set by the operator:

```
System defaults (env vars)
  +-- Tenant settings (JSONB)
      +-- Job-level policy selection
```

#### System Defaults (Environment Variables)

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `RETENTION_DEFAULT_POLICY` | string | `default` | Default policy name |
| `RETENTION_MAX_HOURS` | integer | `8760` (1 year) | Maximum retention any policy can specify |
| `RETENTION_CLEANUP_INTERVAL_SECONDS` | integer | `300` (5 min) | How often the cleanup worker runs |
| `RETENTION_CLEANUP_BATCH_SIZE` | integer | `100` | Jobs processed per cleanup cycle |

#### Tenant Settings

Stored in the `tenants.settings` JSONB column:

```json
{
  "retention": {
    "default_batch_policy_id": "uuid-of-policy",
    "default_realtime_policy_id": "uuid-of-policy",
    "allowed_policy_ids": ["uuid-1", "uuid-2"],
    "max_hours": 720
  }
}
```

- `default_batch_policy_id`: Policy used for batch jobs when none specified
- `default_realtime_policy_id`: Policy used for realtime sessions when none specified
- `allowed_policy_ids`: Optional whitelist (NULL = all tenant policies allowed)
- `max_hours`: Tenant-level cap (cannot exceed system `RETENTION_MAX_HOURS`)

#### Per-Job Policy Selection

Jobs specify retention via the `retention_policy` parameter:

```bash
# By policy name
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@meeting.mp3" \
  -F "retention_policy=hipaa-6yr"

# Or by policy ID
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@meeting.mp3" \
  -F "retention_policy_id=uuid-of-policy"
```

If not specified, the tenant's default policy is used. If no tenant default, the system default is used.

### Data Model

#### Retention Policies Table

```sql
CREATE TABLE retention_policies (
    id                              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id                       UUID REFERENCES tenants(id),
    name                            VARCHAR(100) NOT NULL,
    mode                            VARCHAR(20) NOT NULL,
    hours                           INTEGER,
    scope                           VARCHAR(20) NOT NULL DEFAULT 'all',
    realtime_mode                   VARCHAR(20) NOT NULL DEFAULT 'inherit',
    realtime_hours                  INTEGER,
    delete_realtime_on_enhancement  BOOLEAN NOT NULL DEFAULT true,
    is_system                       BOOLEAN NOT NULL DEFAULT false,
    created_at                      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT unique_policy_name_per_tenant UNIQUE NULLS NOT DISTINCT (tenant_id, name),
    CONSTRAINT valid_mode CHECK (mode IN ('auto_delete', 'keep', 'none')),
    CONSTRAINT valid_scope CHECK (scope IN ('all', 'audio_only')),
    CONSTRAINT valid_realtime_mode CHECK (realtime_mode IN ('inherit', 'auto_delete', 'keep', 'none')),
    CONSTRAINT hours_required_for_auto_delete CHECK (
        mode != 'auto_delete' OR hours IS NOT NULL
    )
);

CREATE INDEX idx_retention_policies_tenant ON retention_policies(tenant_id);
```

#### Jobs Table - New Columns

```sql
ALTER TABLE jobs ADD COLUMN retention_policy_id UUID REFERENCES retention_policies(id);
ALTER TABLE jobs ADD COLUMN retention_mode VARCHAR(20) NOT NULL DEFAULT 'auto_delete';
ALTER TABLE jobs ADD COLUMN retention_hours INTEGER;
ALTER TABLE jobs ADD COLUMN retention_scope VARCHAR(20) NOT NULL DEFAULT 'all';
ALTER TABLE jobs ADD COLUMN purge_after TIMESTAMPTZ;
ALTER TABLE jobs ADD COLUMN purged_at TIMESTAMPTZ;

CREATE INDEX idx_jobs_purge_after ON jobs(purge_after)
    WHERE purge_after IS NOT NULL AND purged_at IS NULL;
```

| Column | Type | Description |
|--------|------|-------------|
| `retention_policy_id` | UUID | Policy used (for audit trail) |
| `retention_mode` | VARCHAR(20) | Snapshotted: `auto_delete`, `keep`, `none` |
| `retention_hours` | INTEGER | Snapshotted hours for auto_delete |
| `retention_scope` | VARCHAR(20) | Snapshotted: `all` or `audio_only` |
| `purge_after` | TIMESTAMPTZ | Computed: `completed_at + retention_hours` |
| `purged_at` | TIMESTAMPTZ | When artifacts were actually deleted |

The `purge_after` column is computed at job completion:

- `auto_delete`: `purge_after = completed_at + interval '{retention_hours} hours'`
- `none`: `purge_after = completed_at` (immediate)
- `keep`: `purge_after = NULL` (never auto-purge)

#### Realtime Sessions Table - New Columns

```sql
ALTER TABLE realtime_sessions ADD COLUMN retention_policy_id UUID REFERENCES retention_policies(id);
ALTER TABLE realtime_sessions ADD COLUMN retention_mode VARCHAR(20);
ALTER TABLE realtime_sessions ADD COLUMN retention_hours INTEGER;
ALTER TABLE realtime_sessions ADD COLUMN purge_after TIMESTAMPTZ;
ALTER TABLE realtime_sessions ADD COLUMN purged_at TIMESTAMPTZ;

CREATE INDEX idx_realtime_sessions_purge_after ON realtime_sessions(purge_after)
    WHERE purge_after IS NOT NULL AND purged_at IS NULL;
```

### API Design

#### Policy Management Endpoints

**Create Policy**

```
POST /v1/retention-policies

{
  "name": "hipaa-6yr",
  "mode": "auto_delete",
  "hours": 52560,
  "scope": "all",
  "realtime_mode": "auto_delete",
  "realtime_hours": 168,
  "delete_realtime_on_enhancement": true
}

Response (201):
{
  "id": "uuid",
  "name": "hipaa-6yr",
  "mode": "auto_delete",
  "hours": 52560,
  "scope": "all",
  "realtime_mode": "auto_delete",
  "realtime_hours": 168,
  "delete_realtime_on_enhancement": true,
  "is_system": false,
  "created_at": "2026-02-13T12:00:00Z"
}
```

**List Policies**

```
GET /v1/retention-policies

Response (200):
{
  "policies": [
    {"id": "...", "name": "default", "is_system": true, ...},
    {"id": "...", "name": "hipaa-6yr", "is_system": false, ...}
  ]
}
```

**Get Policy**

```
GET /v1/retention-policies/{policy_id}
GET /v1/retention-policies/by-name/{name}
```

**Delete Policy**

```
DELETE /v1/retention-policies/{policy_id}

Response: 204 No Content

Errors:
- 400: Policy is in use by jobs
- 400: Cannot delete system policy
- 404: Policy not found
```

#### Job Submission with Retention

**Dalston Native** (`POST /v1/audio/transcriptions`):

```bash
curl -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer dk_xxx" \
  -F "file=@meeting.mp3" \
  -F "retention_policy=hipaa-6yr"
```

**ElevenLabs Compatible** (`POST /v1/speech-to-text`):

Retention parameters are accepted as Dalston extensions. ElevenLabs SDKs ignore unknown fields:

```bash
curl -X POST http://localhost:8000/v1/speech-to-text \
  -H "xi-api-key: dk_xxx" \
  -F "file=@meeting.mp3" \
  -F "retention_policy=zero-retention"
```

If no retention parameter is provided, the tenant/system default policy applies.

#### Realtime with Retention

WebSocket connection parameters:

```
WS /v1/audio/transcriptions/stream?retention_policy=hipaa-6yr&store_audio=true
```

The `retention_policy` parameter is only meaningful when `store_audio=true` or `store_transcript=true`. If storage is disabled (default), there are no artifacts to retain.

#### Job Response with Retention Info

`GET /v1/audio/transcriptions/{id}` includes retention information:

```json
{
  "id": "job_abc123",
  "status": "completed",
  "retention": {
    "policy_id": "uuid",
    "policy_name": "default",
    "mode": "auto_delete",
    "hours": 24,
    "scope": "all",
    "purge_after": "2026-02-14T12:00:00Z",
    "purged_at": null
  }
}
```

After purge:

```json
{
  "id": "job_abc123",
  "status": "completed",
  "retention": {
    "policy_id": "uuid",
    "policy_name": "default",
    "mode": "auto_delete",
    "hours": 24,
    "scope": "all",
    "purge_after": "2026-02-14T12:00:00Z",
    "purged_at": "2026-02-14T12:05:00Z"
  }
}
```

#### Accessing Purged Job Artifacts

Attempting to download a transcript or audio for a purged job returns:

```json
{
  "error": {
    "code": "artifacts_purged",
    "message": "Job artifacts were purged at 2026-02-14T12:05:00Z per retention policy.",
    "purged_at": "2026-02-14T12:05:00Z"
  }
}
```

HTTP status: `410 Gone`

#### Delete Audio Only

**Endpoint:** `DELETE /v1/audio/transcriptions/{job_id}/audio`

Deletes the source audio and intermediate artifacts but preserves the transcript. Common pattern: audio is sensitive, transcript is useful.

**Request:** No body required

**Response (success):** `204 No Content`

**Side effects:**

- Deletes audio files from S3
- Sets `purged_at` if not already set
- Updates job's effective scope to reflect audio was deleted
- Emits `audio.deleted` audit event

**Error responses:**

| Status | Condition |
|--------|-----------|
| 404 | Job not found or wrong tenant |
| 400 | Job not in terminal state |
| 410 | Audio already purged |

#### List Jobs with Retention Filters

`GET /v1/audio/transcriptions` gains optional query parameters:

| Parameter | Type | Description |
|-----------|------|-------------|
| `purged` | boolean | Filter by purge state |
| `retention_policy` | string | Filter by policy name |
| `purge_before` | ISO 8601 | Jobs scheduled to purge before this time |
| `purge_after` | ISO 8601 | Jobs scheduled to purge after this time |

#### Tenant Retention Settings

**Endpoint:** `PATCH /v1/tenants/{tenant_id}/settings`

```json
{
  "retention": {
    "default_batch_policy_id": "uuid",
    "default_realtime_policy_id": "uuid",
    "max_hours": 720
  }
}
```

Requires `admin` scope. Validation ensures `max_hours` does not exceed system `RETENTION_MAX_HOURS`.

### Cleanup Worker

The cleanup worker is a background process that periodically purges expired jobs. It runs as part of the orchestrator process.

#### Sweep Logic

```
Every RETENTION_CLEANUP_INTERVAL_SECONDS:
  1. Query expired batch jobs:
     SELECT * FROM jobs
     WHERE purge_after IS NOT NULL
       AND purge_after <= NOW()
       AND purged_at IS NULL
     ORDER BY purge_after ASC
     LIMIT RETENTION_CLEANUP_BATCH_SIZE

  2. For each expired job:
     a. Delete S3 artifacts based on retention_scope:
        - scope "all":        delete jobs/{id}/audio/*, jobs/{id}/tasks/*, jobs/{id}/transcript.json
        - scope "audio_only": delete jobs/{id}/audio/* (preserves tasks/* and transcript.json)
     b. Delete Redis keys: dalston:task:{task_id}:* for all tasks
     c. Set purged_at = NOW()
     d. Emit audit event: job.purged
     e. Emit webhook event: transcription.purged (to registered endpoints)

  3. Query expired realtime sessions (similar logic)

  4. Log summary: "Purged {n} expired jobs, {m} expired sessions"
```

#### Two-Phase Commit with Redis Locks

The cleanup worker uses a two-phase commit pattern with Redis locks for atomicity:

**Phase 1 (Lock + S3 Deletion):**

1. Acquire Redis lock for the job (`SET NX EX` with 5-minute TTL)
2. If lock acquired, delete S3 artifacts (irreversible operation)

**Phase 2 (DB Update):**

3. Mark job as purged in database (`purged_at = NOW()`)
4. Release Redis lock

**Failure Handling:**

- If Phase 2 fails, the Redis lock expires after TTL and the job is retried on next sweep
- S3 deletion is idempotent, so retrying is safe
- Each job uses a fresh database session to ensure clean transaction state

```python
PURGE_LOCK_JOB_KEY = "dalston:purge_lock:job:{job_id}"
PURGE_LOCK_TTL_SECONDS = 300  # 5 minutes
```

#### Idempotency

The cleanup worker is idempotent:

- If it crashes mid-sweep, jobs where S3 deletion succeeded but `purged_at` wasn't set will be re-processed
- S3 delete on non-existent keys is a no-op
- The batch size limit prevents unbounded work per cycle
- Redis locks prevent multiple workers from processing the same job concurrently

#### Zero-Retention Mode (`none`)

For jobs with `retention_mode = none`, the orchestrator triggers cleanup inline when the job reaches a terminal state:

```
On job completion/failure:
  If retention_mode == "none":
    Delete S3 artifacts immediately
    Set purged_at = NOW()
    Emit audit event: job.purged
```

This ensures zero-retention jobs never have artifacts sitting in S3 between sweep intervals.

### Export Behavior

Exports (SRT, VTT, TXT) are generated dynamically from the stored transcript. They do not have separate retention:

- If transcript exists: export succeeds
- If transcript purged: export returns `410 Gone`

### Webhook Events

The retention system emits webhook events to registered admin endpoints:

| Event | Trigger | Payload |
|-------|---------|---------|
| `transcription.purged` | Job artifacts purged | `{transcription_id, purged_at, scope, policy_name}` |
| `session.purged` | Realtime session artifacts purged | `{session_id, purged_at, scope, policy_name}` |

---

## Plan

### Files to Create

| File | Purpose |
|------|---------|
| `dalston/gateway/services/retention.py` | RetentionService - policy resolution, validation |
| `dalston/gateway/api/v1/retention_policies.py` | Policy CRUD endpoints |
| `dalston/orchestrator/cleanup.py` | Background cleanup worker |
| `alembic/versions/xxx_create_retention_policies.py` | Migration for retention_policies table |
| `alembic/versions/xxx_add_retention_columns.py` | Migration adding columns to jobs/sessions |

### Files to Modify

| File | Change |
|------|--------|
| `dalston/db/models.py` | Add `RetentionPolicyModel`, retention columns on jobs/sessions |
| `dalston/common/models.py` | Add `RetentionMode`, `RetentionScope` enums |
| `dalston/gateway/models/requests.py` | Add retention params to job submission |
| `dalston/gateway/models/responses.py` | Add `RetentionInfo` response model |
| `dalston/gateway/api/v1/transcription.py` | Accept retention_policy, add DELETE audio endpoint |
| `dalston/gateway/api/v1/speech_to_text.py` | Accept retention_policy (Dalston extension) |
| `dalston/gateway/api/v1/realtime.py` | Accept retention_policy for stored sessions |
| `dalston/gateway/api/v1/router.py` | Mount retention_policies router |
| `dalston/gateway/services/jobs.py` | Snapshot policy values, compute purge_after |
| `dalston/gateway/services/storage.py` | Add `delete_job_audio()`, `delete_job_artifacts()` |
| `dalston/orchestrator/handlers.py` | Trigger inline purge for zero-retention jobs |
| `dalston/orchestrator/main.py` | Start cleanup worker as background task |
| `dalston/config.py` | Add retention environment variables |
| `sdk/dalston_sdk/client.py` | Add retention_policy parameter to transcription methods |
| `sdk/dalston_sdk/types.py` | Add retention types |
| `cli/dalston_cli/commands/jobs.py` | Add --retention-policy flag |
| `web/src/api/types.ts` | Add retention types |
| `web/src/pages/JobDetail.tsx` | Display retention info |

### Implementation Tasks

See [M25: Data Retention](../plan/milestones/M25-data-retention.md) for detailed implementation plan.

### Verification

1. **Policy CRUD**: Create, list, get, delete retention policies
2. **Default policy**: Submit job without retention param, verify default applied
3. **Named policy**: Submit job with `retention_policy=hipaa-6yr`, verify policy snapshotted
4. **Auto-delete flow**:
   - Submit job with `retention_hours=1`
   - Complete the job
   - Verify `purge_after` is set correctly
   - Wait for cleanup sweep
   - Verify S3 artifacts deleted, `purged_at` set, job row retained
5. **Zero-retention**: Submit with `retention_policy=zero-retention`, verify immediate purge
6. **Keep mode**: Submit with `retention_policy=keep`, verify `purge_after` is NULL
7. **Audio-only delete**: `DELETE .../audio`, verify audio gone, transcript accessible
8. **Purged job access**: Access transcript of purged job, verify `410 Gone`
9. **ElevenLabs compat**: Submit via `/v1/speech-to-text` with `retention_policy`, verify applied
10. **Realtime retention**: Connect with `retention_policy` and `store_audio=true`, verify retention on session
