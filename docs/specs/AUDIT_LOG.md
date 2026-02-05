# Audit Log

## Strategic

### Goal

Provide an immutable, append-only record of all data lifecycle events in Dalston — who accessed what data, when it was created, modified, or deleted, and by whom. This supports regulatory compliance (GDPR accountability, HIPAA audit requirements, SOC 2 evidence), operational visibility, and incident investigation.

### Scope

This spec covers the audit log data model, the events captured, the internal logging mechanism, and the read-only API for querying audit records.

**In scope:**

- Audit log database schema (append-only table)
- Data lifecycle events (create, access, delete, purge)
- Authentication events (API key usage)
- Retention policy events (changes, overrides)
- Read-only query API for audit records
- Log retention and rotation

**Out of scope:**

- Application-level debug logging (handled by standard Python logging)
- Real-time alerting on audit events (future SIEM integration)
- Audit log export to external systems (future feature)
- Audit log integrity verification / cryptographic chaining (future enterprise feature)

**Related documents:**

- [Data Retention](DATA_RETENTION.md) — Retention and cleanup system that generates audit events
- [ADR-004: Data Retention Strategy](../decisions/ADR-004-data-retention-strategy.md) — Decision context
- [Data Model](batch/DATA_MODEL.md) — Existing database schemas
- [API Authentication](implementations/auth-patterns.md) — API key and tenant model

### User Stories

1. As an **operator**, I want to see who accessed or deleted audio data, for incident investigation
2. As a **compliance officer**, I want an immutable record of all data deletions to prove GDPR compliance
3. As a **tenant admin**, I want to audit API key usage within my organization
4. As an **operator**, I want to verify that the retention cleanup worker is functioning correctly
5. As an **auditor**, I need 12+ months of audit evidence for SOC 2 Type II certification

### Design Principles

1. **Append-only**: Audit log entries are never updated or deleted through application code. This is enforced at the database level.
2. **Fail-open**: Audit logging failures must not block the operation being audited. Log the failure, proceed with the operation.
3. **Minimal overhead**: Audit logging is synchronous but lightweight (single INSERT). It must not meaningfully impact request latency.
4. **Separate retention**: Audit logs have their own retention period, independent of (and longer than) the data they describe.

---

## Tactical

### Data Model

#### Audit Log Table

```sql
CREATE TABLE audit_log (
    id              BIGSERIAL PRIMARY KEY,
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    tenant_id       UUID,
    actor_type      VARCHAR(20) NOT NULL,
    actor_id        TEXT NOT NULL,
    action          VARCHAR(50) NOT NULL,
    resource_type   VARCHAR(30) NOT NULL,
    resource_id     TEXT NOT NULL,
    detail          JSONB,
    ip_address      INET,
    user_agent      TEXT
);

-- Query patterns: "show me all events for this tenant" and "show me all events for this resource"
CREATE INDEX idx_audit_log_tenant_ts ON audit_log(tenant_id, timestamp DESC);
CREATE INDEX idx_audit_log_resource ON audit_log(resource_type, resource_id, timestamp DESC);
CREATE INDEX idx_audit_log_action ON audit_log(action, timestamp DESC);
CREATE INDEX idx_audit_log_actor ON audit_log(actor_id, timestamp DESC);
```

| Column | Type | Description |
|--------|------|-------------|
| `id` | BIGSERIAL | Auto-incrementing, immutable identifier |
| `timestamp` | TIMESTAMPTZ | Server time (UTC) when event occurred |
| `tenant_id` | UUID | Tenant context (null for system-level events) |
| `actor_type` | VARCHAR(20) | `api_key`, `system`, `console_user` |
| `actor_id` | TEXT | API key prefix (e.g., `dk_abc1234`), `cleanup_worker`, `orchestrator`, or console user ID |
| `action` | VARCHAR(50) | Event type (see [Event Catalog](#event-catalog) below) |
| `resource_type` | VARCHAR(30) | `job`, `transcript`, `audio`, `api_key`, `tenant`, `session` |
| `resource_id` | TEXT | The resource identifier (job UUID, key prefix, etc.) |
| `detail` | JSONB | Event-specific metadata (see examples below) |
| `ip_address` | INET | Client IP address (null for system events) |
| `user_agent` | TEXT | Client User-Agent header (null for system events) |

#### Immutability Enforcement

The audit log table is protected against modification at the database level:

```sql
-- Prevent updates
CREATE RULE audit_log_no_update AS ON UPDATE TO audit_log
    DO INSTEAD NOTHING;

-- Prevent deletes (except by retention rotation — see below)
CREATE RULE audit_log_no_delete AS ON DELETE TO audit_log
    DO INSTEAD NOTHING;
```

Audit log rotation (deleting entries older than the retention period) is performed by a dedicated maintenance function that temporarily disables the delete rule. This ensures only the rotation process can remove old entries.

### Event Catalog

#### Job Lifecycle Events

| Action | Trigger | Detail Fields |
|--------|---------|---------------|
| `job.created` | Job submitted via API | `{parameters, retention_mode, retention_hours}` |
| `job.completed` | Job processing finished | `{duration_seconds, pipeline_stages}` |
| `job.failed` | Job processing failed | `{error, stage}` |
| `job.cancelled` | Job cancelled by user | `{previous_status}` |
| `job.deleted` | Job record deleted via DELETE API | `{previous_status}` |
| `job.purged` | Job artifacts removed by cleanup worker | `{retention_mode, retention_hours, scope, artifacts_deleted}` |

#### Audio Events

| Action | Trigger | Detail Fields |
|--------|---------|---------------|
| `audio.uploaded` | Audio file received | `{filename, size_bytes, content_type}` |
| `audio.accessed` | Audio file downloaded | `{format}` |
| `audio.deleted` | Audio deleted via DELETE .../audio | `{scope}` |

#### Transcript Events

| Action | Trigger | Detail Fields |
|--------|---------|---------------|
| `transcript.accessed` | Transcript retrieved via GET | `{format}` |
| `transcript.exported` | Transcript exported (SRT, VTT, etc.) | `{format}` |
| `transcript.deleted` | Transcript deleted | `{}` |

#### Session Events (Real-Time)

| Action | Trigger | Detail Fields |
|--------|---------|---------------|
| `session.started` | WebSocket connection established | `{worker_id, save_session}` |
| `session.ended` | WebSocket connection closed | `{duration_seconds, audio_duration_ms}` |

#### Authentication Events

| Action | Trigger | Detail Fields |
|--------|---------|---------------|
| `api_key.created` | New API key created | `{key_prefix, scopes}` |
| `api_key.revoked` | API key revoked | `{key_prefix}` |
| `api_key.auth_failed` | Authentication attempt with invalid key | `{key_prefix_attempted}` |

#### Configuration Events

| Action | Trigger | Detail Fields |
|--------|---------|---------------|
| `tenant.retention_updated` | Tenant retention settings changed | `{previous, updated}` |

### Audit Logging Service

A lightweight service injected into Gateway and Orchestrator components:

```python
class AuditService:
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
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> None:
        """Append an audit log entry. Never raises — logs errors and continues."""
```

**Integration points:**

- **Gateway middleware**: Extracts `tenant_id`, `actor_type`, `actor_id` (from API key), `ip_address`, and `user_agent` from each request and attaches to a request-scoped context.
- **Gateway endpoints**: Call `audit_service.log()` after successful operations.
- **Orchestrator handlers**: Call `audit_service.log()` for job lifecycle events (completion, failure, purge).
- **Cleanup worker**: Calls `audit_service.log()` for each purged job.

**Fail-open behavior:**

```python
async def log(self, ...):
    try:
        await self._insert(...)
    except Exception:
        logger.error("Failed to write audit log entry", exc_info=True)
        # Do NOT re-raise — audit failure must not block operations
```

### API Design

Audit log endpoints require `admin` scope.

#### List Audit Events

**Endpoint:** `GET /v1/audit`

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `tenant_id` | UUID | current tenant | Filter by tenant |
| `action` | string | — | Filter by action (e.g., `job.purged`) |
| `resource_type` | string | — | Filter by resource type |
| `resource_id` | string | — | Filter by specific resource |
| `actor_id` | string | — | Filter by actor |
| `since` | ISO 8601 | — | Events after this timestamp |
| `until` | ISO 8601 | — | Events before this timestamp |
| `limit` | integer | 50 | Results per page (max 200) |
| `offset` | integer | 0 | Pagination offset |

**Response:**

```json
{
  "events": [
    {
      "id": 12345,
      "timestamp": "2025-02-01T14:00:00Z",
      "tenant_id": "uuid",
      "actor_type": "system",
      "actor_id": "cleanup_worker",
      "action": "job.purged",
      "resource_type": "job",
      "resource_id": "job_abc123",
      "detail": {
        "retention_mode": "auto_delete",
        "retention_hours": 24,
        "scope": "all",
        "artifacts_deleted": 7
      },
      "ip_address": null,
      "user_agent": null
    },
    {
      "id": 12344,
      "timestamp": "2025-02-01T13:59:58Z",
      "tenant_id": "uuid",
      "actor_type": "api_key",
      "actor_id": "dk_abc1234",
      "action": "transcript.accessed",
      "resource_type": "transcript",
      "resource_id": "job_abc123",
      "detail": {
        "format": "json"
      },
      "ip_address": "203.0.113.42",
      "user_agent": "dalston-sdk/1.0.0"
    }
  ],
  "total": 1847,
  "limit": 50,
  "offset": 0
}
```

#### Get Audit Trail for a Resource

**Endpoint:** `GET /v1/audit/resources/{resource_type}/{resource_id}`

Convenience endpoint that returns all audit events for a specific resource, ordered chronologically. Useful for investigating the full lifecycle of a job.

**Response:** Same format as the list endpoint, pre-filtered by resource.

### Console Integration

The web console (`/console`) displays audit events in two contexts:

1. **Job detail page**: Shows the audit trail for that specific job (created → accessed → purged)
2. **Admin audit page**: Searchable/filterable log viewer for tenant admins

### Audit Log Retention

Audit logs have their own retention period, independent of job data:

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `AUDIT_LOG_RETENTION_DAYS` | integer | `365` (1 year) | Days to retain audit log entries |
| `AUDIT_LOG_ROTATION_INTERVAL_HOURS` | integer | `24` | How often the rotation job runs |

The rotation job runs alongside the data retention cleanup worker:

```
Every AUDIT_LOG_ROTATION_INTERVAL_HOURS:
  1. Temporarily disable delete rule on audit_log table
  2. DELETE FROM audit_log WHERE timestamp < NOW() - interval '{AUDIT_LOG_RETENTION_DAYS} days'
  3. Re-enable delete rule
  4. Log: "Rotated {n} audit log entries older than {days} days"
```

For SOC 2 compliance, operators should set `AUDIT_LOG_RETENTION_DAYS` to at least 365. For HIPAA, 2190 (6 years) is recommended.

---

## Plan

### Files to Create

| File | Purpose |
|------|---------|
| `dalston/common/audit.py` | `AuditService` class |
| `dalston/gateway/api/v1/audit.py` | Audit log query endpoints |
| `alembic/versions/xxx_create_audit_log.py` | Migration creating audit_log table with rules |

### Files to Modify

| File | Change |
|------|--------|
| `dalston/gateway/main.py` | Initialize `AuditService`, add to app state |
| `dalston/gateway/middleware/auth.py` | Attach audit context (actor, IP, user-agent) to request |
| `dalston/gateway/api/v1/transcription.py` | Emit audit events on create, access, delete |
| `dalston/gateway/api/v1/elevenlabs.py` | Emit audit events on create, access |
| `dalston/orchestrator/handlers.py` | Emit audit events on job completion, failure, purge |
| `dalston/orchestrator/cleanup.py` | Emit audit events on purge (created in DATA_RETENTION.md) |
| `dalston/gateway/api/console.py` | Add audit log page endpoints |
| `web/src/pages/AuditLog.tsx` | Audit log viewer page (new) |
| `web/src/pages/BatchJobDetail.tsx` | Show job audit trail |

### Implementation Tasks

- [ ] Create `audit_log` table migration with immutability rules
- [ ] Implement `AuditService` in `dalston/common/audit.py`
  - [ ] `log()` method with fail-open behavior
  - [ ] Request-scoped context for actor/IP/user-agent
- [ ] Initialize `AuditService` in gateway startup
- [ ] Add audit context extraction to auth middleware
- [ ] Emit `job.created` on POST transcription
- [ ] Emit `audio.uploaded` on file receipt
- [ ] Emit `transcript.accessed` on GET transcript
- [ ] Emit `transcript.exported` on export download
- [ ] Emit `audio.deleted` on DELETE .../audio
- [ ] Emit `job.deleted` on DELETE job
- [ ] Emit `job.completed`, `job.failed` in orchestrator handlers
- [ ] Emit `job.purged` in cleanup worker
- [ ] Emit `api_key.created`, `api_key.revoked` in key management endpoints
- [ ] Emit `session.started`, `session.ended` in WebSocket handler
- [ ] Implement `GET /v1/audit` query endpoint
- [ ] Implement `GET /v1/audit/resources/{type}/{id}` endpoint
- [ ] Add audit log rotation job
- [ ] Add audit log viewer to web console
- [ ] Add job audit trail to job detail page
- [ ] Unit tests for `AuditService.log()` (including fail-open behavior)
- [ ] Unit tests for audit query endpoint (filtering, pagination)
- [ ] Integration test: submit → access → delete job, verify complete audit trail

### Verification

1. **Basic logging**: Submit a job, retrieve transcript, delete job. Query audit log. Verify all three events appear with correct actor, timestamp, and detail.
2. **Fail-open**: Simulate database error during audit INSERT. Verify the parent operation (job submission, transcript access) succeeds despite audit failure.
3. **Immutability**: Attempt `UPDATE audit_log SET action='tampered'`. Verify the rule blocks the update.
4. **Immutability**: Attempt `DELETE FROM audit_log WHERE id=1`. Verify the rule blocks the delete.
5. **Query API**: Create events across multiple tenants, resources, and actions. Verify all filter combinations return correct results.
6. **Retention rotation**: Set `AUDIT_LOG_RETENTION_DAYS=0`. Run rotation. Verify old entries are removed.
7. **Cleanup worker audit trail**: Let the retention cleanup worker purge a job. Verify `job.purged` event appears in audit log with correct detail fields.
8. **Console**: View job detail page, verify audit trail renders chronologically.
