# Data Retention V2 (Draft, Breaking Change)

## Status

Draft

## Intent

Replace policy-centric retention with an artifact-centric model that:

1. Supports independent retention for audio and transcript (and other artifacts)
2. Removes contradictory combinations at request time
3. Handles batch, realtime, hybrid, and PII consistently
4. Makes defaults explicit and enforceable
5. Is operationally simple to purge and audit

No backward compatibility is required.

---

## Core Concept

Retention is defined per persisted artifact, not per job/session mode.

Each artifact has two fields:

- `store` (boolean): whether artifact is allowed to persist
- `ttl_seconds` (integer or `null`):
  - `null` = keep forever
  - `0` = purge immediately after required processing
  - `>0` = purge after that TTL

This collapses old `keep` / `none` / `auto_delete` into one model.

### Duration Input Format

External API accepts either:

- `ttl_seconds` (integer), or
- `delete_after` (human-readable duration string)

Supported human-readable suffixes:

- `s`, `m`, `h`, `d`, `w`

Examples:

- `"delete_after": "7d"`
- `"delete_after": "30d"`
- `"delete_after": "12h"`
- `"ttl_seconds": 0`
- `"ttl_seconds": null`

Validation rule: request must provide at most one of `ttl_seconds` and `delete_after`.
Server normalizes to `ttl_seconds` before persistence.

---

## Artifact Types

V2 standard artifact types:

- `audio.source`
- `audio.redacted`
- `transcript.raw`
- `transcript.redacted`
- `pii.entities`
- `pipeline.intermediate`
- `realtime.transcript`
- `realtime.events` (optional debug/event log)

---

## Request Model

### Batch `POST /v2/audio/transcriptions`

Request includes `retention` object:

```json
{
  "speaker_detection": "diarize",
  "pii": {
    "enabled": true,
    "redact_audio": true
  },
  "retention": {
    "audio.source": { "store": true, "ttl_seconds": 604800 },
    "audio.redacted": { "store": true, "ttl_seconds": 2592000 },
    "transcript.raw": { "store": false },
    "transcript.redacted": { "store": true, "ttl_seconds": 2592000 },
    "pii.entities": { "store": true, "ttl_seconds": 2592000 },
    "pipeline.intermediate": { "store": false }
  }
}
```

### Realtime

Use explicit create/attach flow instead of overloaded WS query params:

1. `POST /v2/realtime/sessions` with full config + retention
2. Connect websocket with `session_token`

This avoids query-string complexity and validation drift.

#### Realtime Session Token

`POST /v2/realtime/sessions` returns:

- `session_id`
- `session_token`
- `ws_url`
- `expires_at`

Token behavior:

- Signed short-lived token (JWT or equivalent)
- Scope: exactly one `session_id`
- Expiry: 5 minutes by default (configurable)
- One-time use: first successful WS attach consumes token

Token claims (minimum):

- `sub`: tenant id
- `sid`: session id
- `exp`: expiration
- `jti`: unique token id (for one-time replay protection)

WS attach behavior:

- If token expired/used/invalid: close with auth error
- If valid: connection binds to existing persisted session record
- API key is not required on WS query once `session_token` is used

---

## Templates and Defaults

Templates are optional convenience, not the primary model.

### Resolution Order

1. Request `retention` (exact overrides)
2. Tenant default template
3. System default template

### Template Use

- `retention_template_id` may be provided
- `retention` in request overrides template entries
- Missing artifact entries are filled from template/default

### Template Snapshot Semantics

At owner creation time (`job` or `session`), resolved retention is snapshotted into:

- `retention_snapshot`
- artifact rows in `artifact_objects`

If a template is later updated, existing owners are unaffected.
Only new jobs/sessions see the new template values.

### Operator/Tenant Constraints

Hard validation caps:

- `max_ttl_seconds` per artifact type
- `forbidden_store_artifacts` per tenant or environment
- Optional `require_redacted_only_when_pii=true`

Constraint storage location:

- System-level constraints: process config (environment variables)
- Tenant-level constraints: `tenants.settings -> retention_constraints`

Example `tenants.settings.retention_constraints`:

```json
{
  "max_ttl_seconds_by_artifact": {
    "audio.source": 2592000,
    "transcript.raw": 0,
    "transcript.redacted": 31536000
  },
  "forbidden_store_artifacts": ["transcript.raw"],
  "require_redacted_only_when_pii": true
}
```

---

## Validation Rules

Validation happens before job/session creation. Invalid combinations fail with 400.

### General

- Every artifact rule must be normalized to `{store, ttl_seconds}`
- If `store=false`, `ttl_seconds` must be absent
- If `store=true`, `ttl_seconds` may be `null`, `0`, or `>0`

### Pipeline Constraints

- `enhance_on_end=true` requires `audio.source.store=true`
- `pii.redact_audio=true` requires `pii.enabled=true`
- `pii.redact_audio=true` requires `audio.source.store=true` at least until redaction finishes
- `transcript.raw.store=true` with `pii.enabled=true` is allowed only if tenant policy permits raw transcript storage

### Dominance Rules

- `pipeline.intermediate.store` defaults to `false` unless explicitly enabled for debugging
- If `transcript.raw.store=false`, API must never return raw transcript
- If `audio.source.store=false`, download endpoint must return 404/410 immediately after processing

---

## Hybrid Semantics

Hybrid has two owners:

- realtime session artifacts
- enhancement job artifacts

To prevent races, source audio used by enhancement is pinned until enhancement ingest completes.

### Pinning

- `audio.source` gets a temporary processing lock (`locked_until` or dependency row)
- Purge worker skips locked artifacts
- Lock is released when enhancement prepare step confirms ingest

### Recommended Defaults (hybrid)

- `realtime.transcript`: short TTL or disabled
- `audio.source`: short TTL (enough for enhancement)
- `transcript.redacted`: business TTL

---

## PII Semantics

PII requires explicit handling of raw vs redacted data.

### Recommended Safe Defaults

- `transcript.raw.store=false`
- `audio.source.store=true` short TTL (or `0` if no post-processing needed)
- `audio.redacted.store=true` business TTL
- `transcript.redacted.store=true` business TTL
- `pii.entities.store=true` same TTL as redacted transcript (or shorter)

### Security Classification

Each artifact row carries:

- `sensitivity`: `raw_pii`, `redacted`, `metadata`
- Optional `compliance_tags`: `gdpr`, `hipaa`, `pci`

This allows compliance-aware purge reporting.

---

## Data Model

### `artifact_objects`

Canonical persisted-artifact inventory.

```sql
CREATE TABLE artifact_objects (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID NOT NULL REFERENCES tenants(id),
  owner_type VARCHAR(20) NOT NULL,      -- job | session
  owner_id UUID NOT NULL,
  artifact_type VARCHAR(50) NOT NULL,   -- audio.source, transcript.redacted, ...
  uri TEXT NOT NULL,
  sensitivity VARCHAR(20) NOT NULL,     -- raw_pii | redacted | metadata
  store BOOLEAN NOT NULL,
  ttl_seconds INTEGER,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  purge_after TIMESTAMPTZ,
  purged_at TIMESTAMPTZ,
  lock_reason VARCHAR(50),
  lock_until TIMESTAMPTZ,
  UNIQUE(owner_type, owner_id, artifact_type, uri)
);
```

### `retention_templates`

```sql
CREATE TABLE retention_templates (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id UUID REFERENCES tenants(id),
  name VARCHAR(100) NOT NULL,
  is_system BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE NULLS NOT DISTINCT (tenant_id, name)
);
```

### `retention_template_rules`

```sql
CREATE TABLE retention_template_rules (
  template_id UUID NOT NULL REFERENCES retention_templates(id) ON DELETE CASCADE,
  artifact_type VARCHAR(50) NOT NULL,
  store BOOLEAN NOT NULL,
  ttl_seconds INTEGER,
  PRIMARY KEY (template_id, artifact_type)
);
```

### `jobs` / `realtime_sessions`

Replace old retention columns with one immutable snapshot:

- `retention_snapshot JSONB NOT NULL`
- `retention_template_id UUID NULL`

No `retention_mode`, `retention_hours`, `retention_scope`.

### `tenants.settings` (constraints)

Tenant-specific retention constraints live in:

- `tenants.settings.retention_constraints`

Defaults and hard caps remain process-configured for operators.

---

## Purge Worker V2

Single artifact-driven sweep:

```sql
SELECT * FROM artifact_objects
WHERE purge_after <= NOW()
  AND purged_at IS NULL
  AND (lock_until IS NULL OR lock_until < NOW())
ORDER BY purge_after
LIMIT :batch_size;
```

For each artifact:

1. Acquire distributed lock by artifact id
2. Delete object at `uri` (idempotent)
3. Set `purged_at`
4. Emit audit event

No branching by job/session scope required.

During cutover, worker reads only `artifact_objects` as the source of truth.

---

## API Surface (V2)

### Retention Template Admin

- `POST /v2/retention/templates`
- `GET /v2/retention/templates`
- `GET /v2/retention/templates/{id}`
- `DELETE /v2/retention/templates/{id}`
- `POST /v2/retention/templates/{id}/set-default` (tenant default)

### Artifact Visibility

- `GET /v2/jobs/{id}/artifacts`
- `GET /v2/realtime/sessions/{id}/artifacts`

Each artifact entry includes `purge_after`, `purged_at`, `sensitivity`.

---

## Scenario Matrix (Canonical Outcomes)

1. `audio.source:7d`, `transcript.redacted:30d`: independent purge times.
2. `audio.source:0`, `transcript.redacted:30d`: audio removed immediately post-processing, transcript kept.
3. `audio.source:false`, `enhance_on_end:true`: rejected.
4. `transcript.raw:false`, `pii:true`: raw not retrievable, redacted only.
5. `pii.entities:false`: redacted transcript can exist without entity payload persistence.
6. `pipeline.intermediate:false`: tasks/debug payloads do not persist.
7. `audio.redacted:true`, `audio.source:0`: keep only redacted audio.
8. `all store=false`: metadata-only job/session.
9. hybrid session with short source TTL + lock: enhancement succeeds, then source purges.
10. `ttl_seconds=null`: explicit keep forever for that artifact only.

---

## One-Time Data Migration and Cutover

No API backward compatibility is required, but existing persisted data must be migrated so purge continues correctly.

### Cutover Sequence

1. Stop V1 cleanup worker.
2. Run schema migration creating V2 tables.
3. Backfill `retention_snapshot` for existing `jobs` and `realtime_sessions`.
4. Backfill `artifact_objects` for existing persisted artifacts.
5. Start V2 cleanup worker (artifact-driven only).
6. Drop V1 retention columns and V1 endpoints.

### Backfill Rules (V1 -> V2)

Map existing mode/scope to artifact rules:

- `keep` -> `store=true`, `ttl_seconds=null` for retained artifacts
- `none` -> `store=true`, `ttl_seconds=0` for retained artifacts
- `auto_delete + hours` -> `store=true`, `ttl_seconds=hours*3600`

Scope handling for jobs:

- `all`: apply TTL to `audio.source`, `transcript.*`, and `pipeline.intermediate`
- `audio_only`: apply TTL only to `audio.source`; set transcript artifacts to `ttl_seconds=null` unless explicitly overridden by migration policy

Realtime sessions:

- No scope existed in V1; backfill `audio.source` and `realtime.transcript` from session URIs and stored flags

### V1 Data Quality Edge Cases

If V1 has ambiguous records (for example `auto_delete` with missing hours and no computed `purge_after`), migrate to conservative keep:

- `ttl_seconds=null`
- add `migration_warning` metadata in artifact row / audit detail

This avoids accidental destructive deletion.

### Backfill Ownership Discovery

Create artifact rows only for objects that actually exist or were explicitly referenced:

- batch: `jobs.audio_uri`, canonical transcript URI, known redacted audio URI
- realtime: `sessions.audio_uri`, `sessions.transcript_uri`
- optional sweep to register existing `jobs/{id}/tasks/*` as `pipeline.intermediate`

### Post-Cutover Guarantee

After cutover, purge eligibility is determined exclusively from `artifact_objects.purge_after`.
No legacy retention fields are consulted.

---

## Pros / Cons

### Pros

- Matches user mental model (audio and transcript separated)
- Removes mode/scope ambiguity
- Uniform behavior across batch/realtime/hybrid
- Stronger PII guarantees by artifact class
- Purge logic becomes simpler and more observable

### Cons

- Larger schema and API surface
- More validation logic
- Requires client updates (breaking)
- More rows (artifact-level indexing/storage overhead)

---

## Recommended Rollout (Breaking)

1. Implement V2 endpoints and schema only (no compatibility layer)
2. Run one-time backfill of existing jobs/sessions into `artifact_objects`
3. Switch cleanup worker to artifact-only mode
4. Remove V1 retention fields and policy assumptions from runtime code
5. Migrate SDK/CLI to send explicit retention map
6. Update web console to template + per-request override model
7. Add conformance tests for the 10 canonical scenarios above

---

## Non-Goals (V2)

- Legal hold workflow
- Backup crypto-shredding
- Cross-job subject-level erasure API

These can layer on top of artifact-centric retention later.
