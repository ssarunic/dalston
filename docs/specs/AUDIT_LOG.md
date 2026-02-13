# Audit Log

## Strategic

### Goal

Provide an immutable, append-only record of all data lifecycle events in Dalston - who accessed what data, when it was created, modified, or deleted, and by whom. This supports regulatory compliance (GDPR accountability, HIPAA audit requirements, SOC 2 evidence), operational visibility, and incident investigation.

### Scope

This spec covers the audit log data model, the events captured, the internal logging mechanism, and the read-only API for querying audit records.

**In scope:**

- Audit log database schema (append-only table)
- Data lifecycle events (create, access, delete, purge)
- Authentication events (API key usage)
- Retention policy events (creation, application)
- Read-only query API for audit records
- Log retention and rotation
- Integration with existing structured logging (M18)

**Out of scope:**

- Application-level debug logging (handled by structlog via M18)
- Real-time alerting on audit events (future SIEM integration)
- Audit log export to external systems (future feature)
- Audit log integrity verification / cryptographic chaining (future enterprise feature)

**Related documents:**

- [Data Retention](DATA_RETENTION.md) - Retention and cleanup system that generates audit events
- [ADR-008: Data Retention Strategy](../decisions/ADR-008-data-retention-strategy.md) - Decision context
- [Data Model](batch/DATA_MODEL.md) - Existing database schemas
- [Auth Patterns](implementations/auth-patterns.md) - API key and tenant model
- [ADR-005: Unified Logging](../decisions/ADR-005-unified-logging.md) - Structured logging infrastructure

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
5. **Correlation**: Audit events include correlation IDs linking to structured logs for full context.

---

## Tactical

### Data Model

#### Audit Log Table

```sql
CREATE TABLE audit_log (
    id              BIGSERIAL PRIMARY KEY,
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    correlation_id  VARCHAR(36),
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

-- Query patterns
CREATE INDEX idx_audit_log_tenant_ts ON audit_log(tenant_id, timestamp DESC);
CREATE INDEX idx_audit_log_resource ON audit_log(resource_type, resource_id, timestamp DESC);
CREATE INDEX idx_audit_log_action ON audit_log(action, timestamp DESC);
CREATE INDEX idx_audit_log_actor ON audit_log(actor_id, timestamp DESC);
CREATE INDEX idx_audit_log_correlation ON audit_log(correlation_id) WHERE correlation_id IS NOT NULL;
```

| Column | Type | Description |
|--------|------|-------------|
| `id` | BIGSERIAL | Auto-incrementing, immutable identifier |
| `timestamp` | TIMESTAMPTZ | Server time (UTC) when event occurred |
| `correlation_id` | VARCHAR(36) | Links to structured logs (X-Request-ID) |
| `tenant_id` | UUID | Tenant context (null for system-level events) |
| `actor_type` | VARCHAR(20) | `api_key`, `system`, `console_user`, `webhook` |
| `actor_id` | TEXT | API key prefix (e.g., `dk_abc1234`), `cleanup_worker`, `orchestrator`, or user ID |
| `action` | VARCHAR(50) | Event type (see Event Catalog below) |
| `resource_type` | VARCHAR(30) | `job`, `transcript`, `audio`, `api_key`, `tenant`, `session`, `retention_policy` |
| `resource_id` | TEXT | The resource identifier (job UUID, key prefix, etc.) |
| `detail` | JSONB | Event-specific metadata |
| `ip_address` | INET | Client IP address (null for system events) |
| `user_agent` | TEXT | Client User-Agent header (null for system events) |

#### Immutability Enforcement

The audit log table is protected against modification at the database level:

```sql
-- Prevent updates
CREATE RULE audit_log_no_update AS ON UPDATE TO audit_log
    DO INSTEAD NOTHING;

-- Prevent deletes (rotation uses TRUNCATE or partition drop)
CREATE RULE audit_log_no_delete AS ON DELETE TO audit_log
    DO INSTEAD NOTHING;
```

Audit log rotation is performed by a dedicated maintenance function that uses table partitioning or operates outside the application context.

### Event Catalog

#### Job Lifecycle Events

| Action | Trigger | Detail Fields |
|--------|---------|---------------|
| `job.created` | Job submitted via API | `{parameters, retention_policy}` |
| `job.completed` | Job processing finished | `{duration_seconds, pipeline_stages}` |
| `job.failed` | Job processing failed | `{error, stage}` |
| `job.cancelled` | Job cancelled by user | `{previous_status}` |
| `job.deleted` | Job record deleted via DELETE API | `{previous_status}` |
| `job.purged` | Job artifacts removed by retention | `{retention_policy, scope, artifacts_deleted}` |

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

#### Session Events (Real-Time)

| Action | Trigger | Detail Fields |
|--------|---------|---------------|
| `session.started` | WebSocket connection established | `{worker_id, store_audio, store_transcript}` |
| `session.ended` | WebSocket connection closed | `{duration_seconds, audio_duration_ms, status}` |
| `session.purged` | Session artifacts removed by retention | `{retention_policy, scope}` |

#### Authentication Events

| Action | Trigger | Detail Fields |
|--------|---------|---------------|
| `api_key.created` | New API key created | `{key_prefix, scopes}` |
| `api_key.revoked` | API key revoked | `{key_prefix}` |
| `api_key.auth_failed` | Authentication failed | `{key_prefix_attempted, reason}` |

#### Retention Policy Events

| Action | Trigger | Detail Fields |
|--------|---------|---------------|
| `retention_policy.created` | New policy created | `{name, mode, hours, scope}` |
| `retention_policy.deleted` | Policy deleted | `{name}` |

#### Configuration Events

| Action | Trigger | Detail Fields |
|--------|---------|---------------|
| `tenant.settings_updated` | Tenant settings changed | `{changed_fields}` |

### Audit Service

A lightweight service injected into Gateway and Orchestrator components:

```python
class AuditService:
    """Append-only audit log service."""

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
        """Append an audit log entry. Never raises - logs errors and continues."""
```

#### Integration Points

- **Gateway middleware**: Extracts `tenant_id`, `actor_type`, `actor_id` (from API key), `correlation_id`, `ip_address`, and `user_agent` from each request and attaches to request-scoped context.
- **Gateway endpoints**: Call `audit_service.log()` after successful operations.
- **Orchestrator handlers**: Call `audit_service.log()` for job lifecycle events.
- **Cleanup worker**: Calls `audit_service.log()` for each purged job/session.

#### Fail-Open Behavior

```python
async def log(self, ...) -> None:
    try:
        await self._insert(...)
    except Exception:
        # Log to structlog but DO NOT re-raise
        logger.error(
            "audit_log_write_failed",
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            exc_info=True,
        )
        # Operation continues - audit failure must not block business logic
```

#### Correlation with Structured Logs

Audit events include `correlation_id` (the `X-Request-ID` header) to link with detailed structured logs:

```python
# In audit log
{"action": "job.created", "correlation_id": "req_abc123", ...}

# In structured logs (searchable via M18 infrastructure)
{"event": "job_created", "correlation_id": "req_abc123", "parameters": {...}, ...}
```

This allows:

- Audit log: "What happened to this resource?" (compliance view)
- Structured logs: "What was the full context?" (debugging view)

### API Design

Audit log endpoints require `admin` scope.

#### List Audit Events

**Endpoint:** `GET /v1/audit`

**Query Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `tenant_id` | UUID | current tenant | Filter by tenant |
| `action` | string | - | Filter by action (e.g., `job.purged`) |
| `resource_type` | string | - | Filter by resource type |
| `resource_id` | string | - | Filter by specific resource |
| `actor_id` | string | - | Filter by actor |
| `since` | ISO 8601 | - | Events after this timestamp |
| `until` | ISO 8601 | - | Events before this timestamp |
| `limit` | integer | 50 | Results per page (max 200) |
| `cursor` | string | - | Pagination cursor |

**Response:**

```json
{
  "events": [
    {
      "id": 12345,
      "timestamp": "2026-02-13T14:00:00Z",
      "correlation_id": "req_abc123",
      "tenant_id": "uuid",
      "actor_type": "system",
      "actor_id": "cleanup_worker",
      "action": "job.purged",
      "resource_type": "job",
      "resource_id": "job_abc123",
      "detail": {
        "retention_policy": "default",
        "scope": "all",
        "artifacts_deleted": 7
      },
      "ip_address": null,
      "user_agent": null
    }
  ],
  "cursor": "eyJpZCI6MTIzNDV9",
  "has_more": true
}
```

#### Get Audit Trail for a Resource

**Endpoint:** `GET /v1/audit/resources/{resource_type}/{resource_id}`

Convenience endpoint that returns all audit events for a specific resource, ordered chronologically. Useful for investigating the full lifecycle of a job.

**Response:** Same format as the list endpoint, pre-filtered by resource.

### Console Integration

The web console displays audit events in two contexts:

1. **Job detail page**: Shows the audit trail for that specific job (created -> accessed -> purged)
2. **Admin audit page**: Searchable/filterable log viewer for tenant admins

### Audit Log Retention

Audit logs have their own retention period, independent of job data:

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `AUDIT_LOG_RETENTION_DAYS` | integer | `365` (1 year) | Days to retain audit log entries |
| `AUDIT_LOG_ROTATION_ENABLED` | boolean | `true` | Whether to auto-rotate old entries |

**Compliance guidance:**

- SOC 2: Minimum 365 days recommended
- HIPAA: 2190 days (6 years) recommended
- GDPR: Retain as long as needed to demonstrate compliance

Rotation is handled via:

1. **Table partitioning** by month (preferred for large deployments)
2. **Periodic DELETE** with temporary rule suspension (for smaller deployments)

### Metrics

The audit service emits Prometheus metrics (integrates with M20):

| Metric | Type | Description |
|--------|------|-------------|
| `dalston_audit_events_total` | Counter | Total audit events by action |
| `dalston_audit_write_errors_total` | Counter | Failed audit writes |
| `dalston_audit_write_duration_seconds` | Histogram | Audit write latency |

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
| `dalston/gateway/dependencies.py` | Add `get_audit_service` dependency |
| `dalston/gateway/middleware/auth.py` | Attach audit context (actor, IP, user-agent) to request |
| `dalston/gateway/api/v1/transcription.py` | Emit audit events on create, access, delete |
| `dalston/gateway/api/v1/speech_to_text.py` | Emit audit events on create, access |
| `dalston/gateway/api/v1/realtime.py` | Emit session.started, session.ended events |
| `dalston/gateway/api/v1/api_keys.py` | Emit api_key events |
| `dalston/gateway/api/v1/retention_policies.py` | Emit retention_policy events |
| `dalston/gateway/api/v1/router.py` | Mount audit router |
| `dalston/orchestrator/handlers.py` | Emit job completion, failure, purge events |
| `dalston/orchestrator/cleanup.py` | Emit purge events for each job/session |
| `dalston/config.py` | Add audit log environment variables |
| `web/src/App.tsx` | Add audit log route |
| `web/src/pages/AuditLog.tsx` | Audit log viewer page (new) |
| `web/src/pages/JobDetail.tsx` | Show job audit trail |

### Implementation Tasks

See [M25: Data Retention](../plan/milestones/M25-data-retention.md) for detailed implementation plan.

### Verification

1. **Basic logging**: Submit a job, retrieve transcript, delete job. Query audit log. Verify all events appear with correct actor, timestamp, and detail.
2. **Fail-open**: Simulate database error during audit INSERT. Verify the parent operation succeeds despite audit failure.
3. **Immutability**: Attempt `UPDATE audit_log SET action='tampered'`. Verify the rule blocks the update.
4. **Immutability**: Attempt `DELETE FROM audit_log WHERE id=1`. Verify the rule blocks the delete.
5. **Query API**: Create events across multiple tenants, resources, actions. Verify all filter combinations return correct results.
6. **Correlation**: Submit job, find audit event, use correlation_id to find detailed logs.
7. **Cleanup worker trail**: Let retention cleanup worker purge a job. Verify `job.purged` event appears with correct detail fields.
8. **Console**: View job detail page, verify audit trail renders chronologically.
