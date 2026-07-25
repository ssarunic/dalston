# Audit log

Dalston records tenant-scoped security and data-lifecycle events in the
append-only `audit_log` table. Audit events support operational investigation
and compliance evidence; they are not a substitute for application logs,
storage retention, or an external SIEM.

## Data model

Each row contains:

- monotonically increasing ID and timestamp;
- optional request correlation ID and tenant ID;
- actor type/ID;
- action;
- resource type/ID;
- optional structured detail;
- optional client IP and user agent.

The current model is defined by `AuditLogModel` in `dalston/db/models.py`.
Application code creates events through `dalston/common/audit.py`.

## Current actions

The audit service provides events for:

| Area | Actions |
| --- | --- |
| Jobs | `job.created`, `job.renamed`, `job.cancel_requested`, `job.deleted`, `job.purged` |
| Audio/transcript | `audio.uploaded`, `audio.deleted`, `transcript.accessed`, `transcript.exported` |
| Realtime | `session.started`, `session.ended` |
| API keys/auth | `api_key.created`, `api_key.revoked`, `auth.failed`, `permission.denied` |
| Models | `model.downloaded`, `model.download_failed`, `model.removed`, `model.deleted_from_registry` |
| Settings | `settings.updated`, `settings.reset` |

An action existing in `AuditService` means the event shape is supported; the
gateway call sites determine which current API flows emit it. The settings
actions are emitted directly from the console API rather than through an
`AuditService` helper. Add/update an event and its call-site tests together.

Details must contain operational metadata, not full transcripts, raw
credentials, webhook secrets, or detected PII values.

## Query API

Audit reads require the `audit:read`/admin-equivalent permission enforced by
`Permission.AUDIT_READ`.

```http
GET /v1/audit
```

Optional filters:

- `resource_type`
- `resource_id`
- `action`
- `actor_id`
- `start_time` (inclusive)
- `end_time` (exclusive)
- `correlation_id`
- `limit` (1–100, default 25)
- `cursor`
- `sort=timestamp_desc|timestamp_asc`

The response contains `events`, the next `cursor`, and `has_more`.

For one resource:

```http
GET /v1/audit/resources/{resource_type}/{resource_id}
```

This endpoint accepts `limit` and `cursor`. Both routes are tenant-scoped even
for privileged callers; an API client cannot query another tenant merely by
guessing a resource ID.

## Event example

```json
{
  "id": 1234,
  "timestamp": "2026-07-24T12:00:00Z",
  "correlation_id": "c03b5b0d-...",
  "tenant_id": "55a22eaa-...",
  "actor_type": "api_key",
  "actor_id": "dk_abcd1234",
  "action": "job.cancel_requested",
  "resource_type": "job",
  "resource_id": "550e8400-e29b-41d4-a716-446655440000",
  "detail": {"previous_status": "running"},
  "ip_address": "192.0.2.10",
  "user_agent": "dalston-sdk/0.1"
}
```

Fields in `detail` vary by action. Consumers should key automation on `action`
and stable top-level fields and tolerate new detail keys.

## Immutability and retention

The API exposes read-only audit routes; there is no HTTP update/delete route.
Database access and operator privileges can still alter tables, so deployments
needing tamper-evident evidence must add database controls, restricted roles,
backups, and/or external export.

Audit-log retention is an operator/database policy and is separate from job
and session artifact retention. Do not purge audit history solely because the
referenced artifact expired; equally, do not retain sensitive payloads inside
audit details.

## Adding an audited operation

1. Add or reuse a focused method on `AuditService`.
2. Emit it only after the authoritative state change succeeds (or use the
   explicit failure event where appropriate).
3. Pass tenant, actor, correlation, and request metadata.
4. Keep details minimal and non-secret.
5. Add tests for action/resource IDs and tenant isolation.
6. Update this action inventory if the event is user-visible.
