# M25: Data Retention & Audit Logging

|               |                                                                                           |
| ------------- | ----------------------------------------------------------------------------------------- |
| **Goal**      | Automated lifecycle management for artifacts with configurable retention policies         |
| **Duration**  | 5-6 days                                                                                  |
| **Dependencies** | M11 (API Authentication), M21 (Admin Webhooks)                                         |
| **Deliverable** | Retention policies, cleanup worker, audit logging, API extensions                       |
| **Status**    | Not Started                                                                               |

## User Story

> *"As an operator, I want to automatically delete old audio files and transcripts after a configurable period, so I can control storage costs and comply with data retention regulations like GDPR and HIPAA."*

---

## Overview

```text
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         DATA RETENTION SYSTEM                                    │
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────────┐│
│  │                       RETENTION POLICIES                                     ││
│  │                                                                              ││
│  │   System Policies (immutable):                                               ││
│  │   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                       ││
│  │   │   default    │  │zero-retention│  │    keep      │                       ││
│  │   │ auto_delete  │  │    none      │  │    keep      │                       ││
│  │   │   24 hours   │  │  immediate   │  │   forever    │                       ││
│  │   └──────────────┘  └──────────────┘  └──────────────┘                       ││
│  │                                                                              ││
│  │   Tenant Policies (custom):                                                  ││
│  │   ┌──────────────┐  ┌──────────────┐                                         ││
│  │   │  hipaa-6yr   │  │  dev-testing │                                         ││
│  │   │ auto_delete  │  │ auto_delete  │                                         ││
│  │   │ 52560 hours  │  │   1 hour     │                                         ││
│  │   └──────────────┘  └──────────────┘                                         ││
│  └─────────────────────────────────────────────────────────────────────────────┘│
│                                    │                                             │
│                      Job references policy at creation                           │
│                                    ▼                                             │
│  ┌─────────────────────────────────────────────────────────────────────────────┐│
│  │                           JOB LIFECYCLE                                      ││
│  │                                                                              ││
│  │   Created ──► Running ──► Completed ──► purge_after ──► Purged              ││
│  │                               │              │              │                ││
│  │                               │              │              │                ││
│  │                     retention_policy    Cleanup Worker   purged_at set       ││
│  │                      snapshotted         deletes S3       job row stays      ││
│  │                                                                              ││
│  └─────────────────────────────────────────────────────────────────────────────┘│
│                                    │                                             │
│                           Audit events emitted                                   │
│                                    ▼                                             │
│  ┌─────────────────────────────────────────────────────────────────────────────┐│
│  │                          AUDIT LOG                                           ││
│  │                                                                              ││
│  │   job.created ──► transcript.accessed ──► job.purged                         ││
│  │        │                   │                    │                            ││
│  │   correlation_id      correlation_id       artifacts_deleted                 ││
│  │   actor: dk_xxx       actor: dk_xxx        actor: cleanup_worker             ││
│  │                                                                              ││
│  └─────────────────────────────────────────────────────────────────────────────┘│
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## Design Decisions

### Named Policies vs Inline Parameters

We use **named retention policies** instead of inline parameters for each job. See [ADR-008](../../decisions/ADR-008-data-retention-strategy.md) for full rationale.

**Benefits:**

- Reusable across jobs ("use the hipaa-6yr policy")
- Clean API (`retention_policy: "hipaa"` vs 3+ fields)
- Auditable lineage
- Compliance-friendly

### Job Rows Persist After Purge

The retention system **only deletes S3 artifacts**. Job rows in PostgreSQL are marked with `purged_at` but retained for:

- Billing records
- Audit trail
- Historical analytics

Explicit `DELETE /v1/audio/transcriptions/{id}` removes the row.

### Fail-Open Audit Logging

Audit log writes must not block business operations. If the audit INSERT fails, log the error and continue.

---

## Steps

### 25.1: Database Schema

**Deliverables:**

- Create `retention_policies` table migration
- Add retention columns to `jobs` table
- Add retention columns to `realtime_sessions` table
- Create audit_log table with immutability rules
- Add `RetentionPolicyModel` to `dalston/db/models.py`

**Schema highlights:**

```sql
-- Retention policies
CREATE TABLE retention_policies (
    id UUID PRIMARY KEY,
    tenant_id UUID REFERENCES tenants(id),
    name VARCHAR(100) NOT NULL,
    mode VARCHAR(20) NOT NULL,
    hours INTEGER,
    scope VARCHAR(20) NOT NULL DEFAULT 'all',
    realtime_mode VARCHAR(20) NOT NULL DEFAULT 'inherit',
    realtime_hours INTEGER,
    delete_realtime_on_enhancement BOOLEAN NOT NULL DEFAULT true,
    is_system BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE NULLS NOT DISTINCT (tenant_id, name)
);

-- Jobs retention columns
ALTER TABLE jobs ADD COLUMN retention_policy_id UUID REFERENCES retention_policies(id);
ALTER TABLE jobs ADD COLUMN retention_mode VARCHAR(20) NOT NULL DEFAULT 'auto_delete';
ALTER TABLE jobs ADD COLUMN retention_hours INTEGER;
ALTER TABLE jobs ADD COLUMN retention_scope VARCHAR(20) NOT NULL DEFAULT 'all';
ALTER TABLE jobs ADD COLUMN purge_after TIMESTAMPTZ;
ALTER TABLE jobs ADD COLUMN purged_at TIMESTAMPTZ;

-- Audit log (immutable)
CREATE TABLE audit_log (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    correlation_id VARCHAR(36),
    tenant_id UUID,
    actor_type VARCHAR(20) NOT NULL,
    actor_id TEXT NOT NULL,
    action VARCHAR(50) NOT NULL,
    resource_type VARCHAR(30) NOT NULL,
    resource_id TEXT NOT NULL,
    detail JSONB,
    ip_address INET,
    user_agent TEXT
);

CREATE RULE audit_log_no_update AS ON UPDATE TO audit_log DO INSTEAD NOTHING;
CREATE RULE audit_log_no_delete AS ON DELETE TO audit_log DO INSTEAD NOTHING;
```

---

### 25.2: Common Types & Enums

**Deliverables:**

- Add `RetentionMode` enum (`auto_delete`, `keep`, `none`) to `dalston/common/models.py`
- Add `RetentionScope` enum (`all`, `audio_only`) to `dalston/common/models.py`
- Add types to `sdk/dalston_sdk/types.py`
- Add types to `web/src/api/types.ts`

---

### 25.3: Retention Service

**Deliverables:**

- Create `dalston/gateway/services/retention.py` with `RetentionService`

**Service methods:**

```python
class RetentionService:
    async def create_policy(self, tenant_id: UUID, data: CreatePolicyRequest) -> RetentionPolicy
    async def list_policies(self, tenant_id: UUID) -> list[RetentionPolicy]
    async def get_policy(self, tenant_id: UUID, policy_id: UUID) -> RetentionPolicy | None
    async def get_policy_by_name(self, tenant_id: UUID, name: str) -> RetentionPolicy | None
    async def delete_policy(self, tenant_id: UUID, policy_id: UUID) -> None
    async def resolve_policy(self, tenant_id: UUID, policy_name: str | None) -> RetentionPolicy
    async def validate_policy_hours(self, tenant_id: UUID, hours: int) -> None
```

**Policy resolution logic:**

1. If `policy_name` provided: look up by name (tenant policies + system policies)
2. If not provided: use tenant's `default_batch_policy_id`
3. If no tenant default: use system `default` policy

---

### 25.4: Retention Policy API

**Deliverables:**

- Create `dalston/gateway/api/v1/retention_policies.py`
- Mount router in `dalston/gateway/api/v1/router.py`
- Add response models to `dalston/gateway/models/responses.py`

**Endpoints:**

```
POST   /v1/retention-policies           Create policy
GET    /v1/retention-policies           List policies (tenant + system)
GET    /v1/retention-policies/{id}      Get policy by ID
GET    /v1/retention-policies/by-name/{name}  Get policy by name
DELETE /v1/retention-policies/{id}      Delete policy (if not in use)
```

---

### 25.5: Job Submission with Retention

**Deliverables:**

- Add `retention_policy` param to `POST /v1/audio/transcriptions`
- Add `retention_policy` param to `POST /v1/speech-to-text` (ElevenLabs compat)
- Snapshot policy values into job record on creation
- Add `RetentionInfo` to job responses

**Submission flow:**

```python
async def create_job(..., retention_policy: str | None = None):
    # 1. Resolve policy
    policy = await retention_service.resolve_policy(tenant_id, retention_policy)

    # 2. Create job with snapshotted values
    job = JobModel(
        ...,
        retention_policy_id=policy.id,
        retention_mode=policy.mode,
        retention_hours=policy.hours,
        retention_scope=policy.scope,
    )

    # 3. Emit audit event
    await audit_service.log("job.created", "job", job.id, ...)
```

---

### 25.6: Purge Timestamp Computation

**Deliverables:**

- Update `JobsService` to compute `purge_after` on job completion
- Handle zero-retention inline purge in orchestrator

**Completion handler:**

```python
async def handle_job_completed(job_id: UUID):
    job = await get_job(job_id)

    if job.retention_mode == "auto_delete":
        job.purge_after = job.completed_at + timedelta(hours=job.retention_hours)
    elif job.retention_mode == "none":
        # Immediate purge
        await purge_job_artifacts(job)
        job.purged_at = datetime.now(UTC)
    # mode == "keep": purge_after stays NULL

    await db.commit()
```

---

### 25.7: Audit Service

**Deliverables:**

- Create `dalston/common/audit.py` with `AuditService`
- Initialize in gateway startup
- Add `get_audit_service` dependency
- Integrate with auth middleware for actor context

**Service implementation:**

```python
class AuditService:
    def __init__(self, db_session_factory):
        self.db_session_factory = db_session_factory

    async def log(
        self,
        action: str,
        resource_type: str,
        resource_id: str,
        *,
        tenant_id: UUID | None = None,
        actor_type: str = "system",
        actor_id: str = "unknown",
        detail: dict | None = None,
        correlation_id: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        try:
            async with self.db_session_factory() as session:
                entry = AuditLogModel(...)
                session.add(entry)
                await session.commit()
        except Exception:
            logger.error("audit_log_write_failed", action=action, exc_info=True)
            # Do NOT re-raise - fail open
```

---

### 25.8: Audit Events Integration

**Deliverables:**

- Add audit logging to transcription endpoints (create, access, delete)
- Add audit logging to realtime session events
- Add audit logging to API key management
- Add audit logging to retention policy management

**Events to emit:**

| Endpoint | Events |
|----------|--------|
| `POST /v1/audio/transcriptions` | `job.created`, `audio.uploaded` |
| `GET /v1/audio/transcriptions/{id}` | (none - metadata only) |
| `GET /v1/audio/transcriptions/{id}/transcript` | `transcript.accessed` |
| `GET /v1/audio/transcriptions/{id}/export` | `transcript.exported` |
| `DELETE /v1/audio/transcriptions/{id}/audio` | `audio.deleted` |
| `DELETE /v1/audio/transcriptions/{id}` | `job.deleted` |
| `WS /v1/audio/transcriptions/stream` (connect) | `session.started` |
| `WS /v1/audio/transcriptions/stream` (close) | `session.ended` |

---

### 25.9: Cleanup Worker

**Deliverables:**

- Create `dalston/orchestrator/cleanup.py` with cleanup worker
- Start cleanup worker in orchestrator main loop
- Add config for cleanup interval and batch size

**Worker implementation:**

```python
async def cleanup_expired_jobs():
    """Periodic sweep of expired jobs."""
    while True:
        await asyncio.sleep(settings.retention_cleanup_interval_seconds)

        expired_jobs = await db.execute(
            select(JobModel)
            .where(JobModel.purge_after <= func.now())
            .where(JobModel.purged_at.is_(None))
            .order_by(JobModel.purge_after)
            .limit(settings.retention_cleanup_batch_size)
        )

        for job in expired_jobs.scalars():
            try:
                await purge_job_artifacts(job)
                job.purged_at = datetime.now(UTC)
                await audit_service.log("job.purged", "job", str(job.id), ...)
                await db.commit()
            except Exception:
                logger.error("cleanup_job_failed", job_id=job.id, exc_info=True)
                await db.rollback()

        logger.info("cleanup_sweep_complete", jobs_purged=len(list(expired_jobs)))
```

---

### 25.10: Storage Deletion Methods

**Deliverables:**

- Add `delete_job_audio()` to `StorageService` (audio + intermediates only)
- Add `delete_job_artifacts()` to `StorageService` (all artifacts)
- Add `delete_session_artifacts()` to `StorageService`

**Deletion paths:**

| Scope | Paths Deleted |
|-------|---------------|
| `all` | `jobs/{id}/audio/*`, `jobs/{id}/tasks/*`, `jobs/{id}/transcript.json` |
| `audio_only` | `jobs/{id}/audio/*`, `jobs/{id}/tasks/*` |

---

### 25.11: Delete Audio Endpoint

**Deliverables:**

- Add `DELETE /v1/audio/transcriptions/{id}/audio` endpoint
- Validate job is in terminal state
- Delete audio, preserve transcript
- Emit audit event

**Endpoint behavior:**

```
DELETE /v1/audio/transcriptions/{job_id}/audio

Response: 204 No Content

Errors:
- 404: Job not found
- 400: Job not in terminal state
- 410: Audio already purged
```

---

### 25.12: Audit Query API

**Deliverables:**

- Create `dalston/gateway/api/v1/audit.py`
- Implement `GET /v1/audit` with filtering
- Implement `GET /v1/audit/resources/{type}/{id}` for resource trail
- Require `admin` scope

---

### 25.13: Realtime Retention

**Deliverables:**

- Add `retention_policy` WebSocket connection parameter
- Apply retention to stored sessions
- Include sessions in cleanup worker sweep
- Handle `delete_realtime_on_enhancement` for hybrid mode

---

### 25.14: SDK & CLI Integration

**Deliverables:**

**SDK (`sdk/dalston_sdk/`):**

- Add `retention_policy` parameter to `transcribe()` and `transcribe_async()`
- Add `RetentionPolicy` type
- Add `list_retention_policies()`, `create_retention_policy()` methods

**CLI (`cli/dalston_cli/`):**

- Add `--retention-policy` flag to `transcribe` command
- Add `dalston retention-policies list|create|delete` commands

---

### 25.15: Console Integration

**Deliverables:**

- Add retention info to job detail page
- Add audit trail section to job detail page
- Add audit log viewer page (`/console/audit`)
- Add retention policy management page (`/console/settings/retention`)

---

### 25.16: Tests

**Deliverables:**

**Unit tests:**

- Retention policy CRUD
- Policy resolution (tenant -> system fallback)
- Purge timestamp computation
- Audit service (including fail-open behavior)
- Cleanup worker sweep logic

**Integration tests:**

- Submit job with retention policy, verify snapshotted
- Submit with `zero-retention`, verify immediate purge
- Submit with `keep`, verify no purge_after
- Wait for cleanup sweep, verify artifacts deleted
- Access purged job transcript, verify 410 Gone
- Delete audio only, verify transcript remains
- Verify audit trail for job lifecycle

---

## Verification

```bash
# Create a retention policy
curl -X POST http://localhost:8000/v1/retention-policies \
  -H "Authorization: Bearer dk_xxx" \
  -H "Content-Type: application/json" \
  -d '{"name": "short-test", "mode": "auto_delete", "hours": 1, "scope": "all"}'

# Submit job with policy
JOB_ID=$(curl -s -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "Authorization: Bearer dk_xxx" \
  -F "file=@test.mp3" \
  -F "retention_policy=short-test" | jq -r '.id')

# Check job has retention info
curl http://localhost:8000/v1/audio/transcriptions/$JOB_ID \
  -H "Authorization: Bearer dk_xxx" | jq '.retention'
# {"policy_name": "short-test", "mode": "auto_delete", "hours": 1, ...}

# Wait for job completion, check purge_after is set
curl http://localhost:8000/v1/audio/transcriptions/$JOB_ID \
  -H "Authorization: Bearer dk_xxx" | jq '.retention.purge_after'
# "2026-02-13T13:00:00Z"

# Check audit trail
curl "http://localhost:8000/v1/audit?resource_type=job&resource_id=$JOB_ID" \
  -H "Authorization: Bearer dk_xxx" | jq '.events[].action'
# "job.created"
# "transcript.accessed"

# Test zero-retention
JOB_ID=$(curl -s -X POST http://localhost:8000/v1/audio/transcriptions \
  -F "file=@test.mp3" \
  -F "retention_policy=zero-retention" | jq -r '.id')
# Wait for completion...
curl http://localhost:8000/v1/audio/transcriptions/$JOB_ID/transcript
# 410 Gone: "artifacts_purged"

# Test audio-only delete
curl -X DELETE http://localhost:8000/v1/audio/transcriptions/$JOB_ID/audio
# 204 No Content
curl http://localhost:8000/v1/audio/transcriptions/$JOB_ID/transcript
# 200 OK (transcript still accessible)
```

---

## Checkpoint

- [ ] `retention_policies` table created with system policies seeded
- [ ] Jobs table has retention columns with proper indexes
- [ ] `audit_log` table created with immutability rules
- [ ] `RetentionService` resolves policies correctly (tenant -> system fallback)
- [ ] Policy CRUD API working
- [ ] Job submission accepts `retention_policy` parameter
- [ ] Job response includes `retention` block
- [ ] `purge_after` computed correctly on job completion
- [ ] Zero-retention jobs purged immediately on completion
- [ ] Cleanup worker runs on schedule and purges expired jobs
- [ ] S3 artifacts deleted, job row retained with `purged_at`
- [ ] `410 Gone` returned for purged job transcripts
- [ ] `DELETE .../audio` deletes audio, preserves transcript
- [ ] `AuditService` logs events with fail-open behavior
- [ ] Audit query API returns filtered events
- [ ] Audit immutability enforced (UPDATE/DELETE blocked)
- [ ] ElevenLabs endpoint accepts `retention_policy`
- [ ] Realtime sessions support retention policies
- [ ] SDK has `retention_policy` parameter
- [ ] CLI has `--retention-policy` flag
- [ ] Console shows retention info on job detail
- [ ] Console has audit log viewer
- [ ] All tests passing

---

## Files Changed

| File | Description |
|------|-------------|
| `alembic/versions/xxx_create_retention_policies.py` | Migration for retention_policies table |
| `alembic/versions/xxx_add_retention_columns.py` | Migration for jobs/sessions retention columns |
| `alembic/versions/xxx_create_audit_log.py` | Migration for audit_log table |
| `dalston/db/models.py` | Add `RetentionPolicyModel`, `AuditLogModel`, retention columns |
| `dalston/common/models.py` | Add `RetentionMode`, `RetentionScope` enums |
| `dalston/common/audit.py` | `AuditService` implementation |
| `dalston/config.py` | Retention and audit config variables |
| `dalston/gateway/services/retention.py` | `RetentionService` implementation |
| `dalston/gateway/api/v1/retention_policies.py` | Policy CRUD endpoints |
| `dalston/gateway/api/v1/audit.py` | Audit query endpoints |
| `dalston/gateway/api/v1/transcription.py` | Retention param, delete audio endpoint, audit events |
| `dalston/gateway/api/v1/speech_to_text.py` | Retention param (ElevenLabs compat) |
| `dalston/gateway/api/v1/realtime.py` | Retention param for stored sessions |
| `dalston/gateway/api/v1/router.py` | Mount new routers |
| `dalston/gateway/models/responses.py` | `RetentionInfo`, `AuditEvent` models |
| `dalston/gateway/services/jobs.py` | Snapshot policy, compute purge_after |
| `dalston/gateway/services/storage.py` | `delete_job_audio()`, `delete_job_artifacts()` |
| `dalston/gateway/main.py` | Initialize AuditService |
| `dalston/gateway/dependencies.py` | `get_audit_service` dependency |
| `dalston/orchestrator/cleanup.py` | Cleanup worker implementation |
| `dalston/orchestrator/handlers.py` | Inline purge for zero-retention, audit events |
| `dalston/orchestrator/main.py` | Start cleanup worker |
| `sdk/dalston_sdk/types.py` | Retention types |
| `sdk/dalston_sdk/client.py` | `retention_policy` param, policy methods |
| `cli/dalston_cli/commands/transcribe.py` | `--retention-policy` flag |
| `cli/dalston_cli/commands/retention.py` | Policy management commands |
| `web/src/api/types.ts` | Retention types |
| `web/src/pages/JobDetail.tsx` | Retention info, audit trail |
| `web/src/pages/AuditLog.tsx` | Audit log viewer |

---

## Unblocked

This milestone enables:

- **GDPR compliance**: Documented retention, erasure capability, audit trail
- **HIPAA readiness**: Configurable long-term retention, audit logging
- **SOC 2 evidence**: Immutable audit log for compliance audits
- **Cost management**: Automatic cleanup prevents unbounded storage growth
