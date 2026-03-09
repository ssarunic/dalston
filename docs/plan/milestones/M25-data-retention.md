# M25: Data Retention & Audit Logging

|               |                                                                                           |
| ------------- | ----------------------------------------------------------------------------------------- |
| **Goal**      | Automated lifecycle management for artifacts with configurable retention policies         |
| **Duration**  | 5-6 days                                                                                  |
| **Dependencies** | M11 (API Authentication), M21 (Admin Webhooks)                                         |
| **Deliverable** | Retention policies, cleanup worker, audit logging, API extensions                       |
| **Status**    | Completed                                                                                 |

## User Story

> *"As an operator, I want to automatically delete old audio files and transcripts after a configurable period, so I can control storage costs and comply with data retention regulations like GDPR and HIPAA."*

---

## Overview

The data retention system has three layers: **retention policies** (system-immutable policies like `default`/24h, `zero-retention`/immediate, and `keep`/forever, plus custom tenant policies), a **job lifecycle** where the policy is snapshotted at creation and a cleanup worker purges S3 artifacts after `purge_after` (keeping the job row with `purged_at` set), and an **audit log** that records events (`job.created`, `transcript.accessed`, `job.purged`) with correlation IDs and actor context.

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

The migration creates three schema changes: a `retention_policies` table (with tenant scoping, mode/hours/scope columns, and system policy flag), retention columns on the `jobs` table (`retention_policy_id`, `retention_mode`, `retention_hours`, `retention_scope`, `purge_after`, `purged_at`), and an immutable `audit_log` table with PostgreSQL rules blocking UPDATE and DELETE. See `alembic/versions/` for the migration files.

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

`RetentionService` in `dalston/gateway/services/retention.py` provides CRUD for policies plus a `resolve_policy()` method that determines the effective policy for a job.

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

On submission, the resolved policy's mode, hours, and scope are snapshotted into the job record so that later policy changes do not affect in-flight jobs. An audit event is emitted on creation. See `dalston/gateway/services/jobs.py`.

---

### 25.6: Purge Timestamp Computation

**Deliverables:**

- Update `JobsService` to compute `purge_after` on job completion
- Handle zero-retention inline purge in orchestrator

On job completion, `purge_after` is computed as `completed_at + retention_hours` for `auto_delete` mode. `none` mode triggers immediate inline purge. `keep` mode leaves `purge_after` NULL. See `dalston/gateway/services/jobs.py` and `dalston/orchestrator/handlers.py`.

---

### 25.7: Audit Service

**Deliverables:**

- Create `dalston/common/audit.py` with `AuditService`
- Initialize in gateway startup
- Add `get_audit_service` dependency
- Integrate with auth middleware for actor context

`AuditService` in `dalston/common/audit.py` provides a `log()` method that writes audit entries with action, resource, actor, correlation ID, and optional detail. It follows the fail-open pattern: if the audit INSERT fails, the error is logged but the business operation proceeds unblocked.

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
- Implement Redis-based two-phase commit for atomicity

**Worker implementation:**

The cleanup worker uses a two-phase commit pattern with Redis locks to ensure atomicity:

1. **Phase 1 (Lock + S3 deletion)**: Acquire Redis lock, then delete S3 artifacts (irreversible)
2. **Phase 2 (DB update)**: Mark job as purged in database

If Phase 2 fails, the Redis lock expires (5 minute TTL) and the job is retried on next sweep. S3 deletion is idempotent so retry is safe.

The cleanup worker queries jobs where `purge_after <= now()` and `purged_at IS NULL`, processing them in configurable batches. It uses Redis locks (5-minute TTL) per job for distributed coordination. See `dalston/orchestrator/cleanup.py`.

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
| `audio_only` | `jobs/{id}/audio/*` (preserves `tasks/*` and `transcript.json`) |

---

### 25.11: Delete Audio Endpoint

**Deliverables:**

- Add `DELETE /v1/audio/transcriptions/{id}/audio` endpoint
- Validate job is in terminal state
- Delete audio, preserve transcript
- Emit audit event

The endpoint returns 204 on success, 404 if the job is not found, 400 if the job is not in a terminal state, or 410 if audio was already purged. See `dalston/gateway/api/v1/transcription.py`.

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

- Create a retention policy via `POST /v1/retention-policies` and confirm it appears in `GET /v1/retention-policies`
- Submit a job with `retention_policy=short-test`, verify the job response includes a `retention` block with snapshotted values and `purge_after` is set after completion
- Submit a job with `retention_policy=zero-retention`, wait for completion, confirm `GET .../transcript` returns 410 Gone
- Call `DELETE /v1/audio/transcriptions/{id}/audio`, confirm 204 and that the transcript remains accessible
- Query `GET /v1/audit?resource_type=job&resource_id={id}` and verify audit events (`job.created`, `transcript.accessed`, etc.) are present

---

## Checkpoint

- [x] `retention_policies` table created with system policies seeded
- [x] Jobs table has retention columns with proper indexes
- [x] `audit_log` table created with immutability rules
- [x] `RetentionService` resolves policies correctly (tenant -> system fallback)
- [x] Policy CRUD API working
- [x] Job submission accepts `retention_policy` parameter
- [x] Job response includes `retention` block
- [x] `purge_after` computed correctly on job completion
- [x] Zero-retention jobs purged immediately on completion
- [x] Cleanup worker runs on schedule and purges expired jobs
- [x] S3 artifacts deleted, job row retained with `purged_at`
- [x] `410 Gone` returned for purged job transcripts
- [x] `DELETE .../audio` deletes audio, preserves transcript
- [x] `AuditService` logs events with fail-open behavior
- [x] Audit query API returns filtered events (cursor-based pagination)
- [x] Audit immutability enforced (UPDATE/DELETE blocked)
- [x] ElevenLabs endpoint accepts `retention_policy`
- [x] Realtime sessions support retention policies
- [x] SDK has `retention_policy` parameter
- [x] CLI has `--retention-policy` flag
- [x] Console shows retention info on job detail
- [x] Console has audit log viewer
- [x] All tests passing

---

## Unblocked

This milestone enables:

- **GDPR compliance**: Documented retention, erasure capability, audit trail
- **HIPAA readiness**: Configurable long-term retention, audit logging
- **SOC 2 evidence**: Immutable audit log for compliance audits
- **Cost management**: Automatic cleanup prevents unbounded storage growth
