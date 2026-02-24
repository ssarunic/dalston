# ADR-008: Data Retention Strategy

## Status

**Superseded** - Replaced by simplified integer-based retention model (February 2026).

The named retention policies approach was replaced with a simpler integer-based model:

- `0` = transient (no storage)
- `-1` = permanent (never delete)
- `1-3650` = days until purge

See [DATA_RETENTION.md](../specs/DATA_RETENTION.md) for the current implementation.

---

## Original Proposal (Historical)

## Context

Dalston processes sensitive audio recordings and produces transcripts that may contain personal, medical, or legally privileged information. Currently, all job artifacts (uploaded audio, intermediate processing files, final transcripts) persist indefinitely in S3 with no automated cleanup, no configurable retention, and no audit trail of data access or deletion.

This creates several problems:

- **Storage costs** grow unbounded as jobs accumulate
- **Privacy compliance** (GDPR, HIPAA, CCPA) requires data minimization, documented retention periods, and the ability to honor erasure requests
- **Security posture** is weakened by retaining sensitive data longer than necessary
- **Operator control** is limited - self-hosted deployments have no mechanism to enforce data lifecycle policies

Industry analysis of major transcription providers reveals a clear pattern:

| Provider | Default | Zero-Retention Option |
|----------|---------|----------------------|
| Deepgram | Zero for real-time | Yes (default for streaming) |
| Google STT | Zero (streaming), ~5 days (async) | Yes |
| Azure Speech | Zero (real-time), configurable TTL (batch) | Yes |
| AssemblyAI | Zero (streaming), 3-day TTL (BAA) | Yes |
| Rev.ai | 30 days then hard delete | No |

The common pattern: **short-lived by default, configurable per job, with a zero-retention option for privacy-sensitive workloads**.

## Options Considered

### 1. Operator-Only Global TTL

A single system-wide retention period (e.g., `RETENTION_HOURS=24`). All jobs purged after the same interval.

**Pros:**

- Simplest implementation - one cron job, one config value
- Easy to reason about

**Cons:**

- No per-job flexibility (a user can't keep some jobs and purge others)
- No selective deletion (can't delete audio but keep transcript)
- Doesn't support zero-retention or indefinite-keep use cases
- Not aligned with how any major provider does it

### 2. Per-Job Inline Parameters

Each job carries its own retention settings as columns (`retention_mode`, `retention_hours`, `retention_scope`), with system-wide and per-tenant defaults.

**Pros:**

- Flexible - each job can have different retention
- Matches AssemblyAI, Azure patterns

**Cons:**

- Parameter sprawl on job submission (3+ extra fields)
- No reusability - same settings repeated across jobs
- Harder to audit ("show me all jobs with HIPAA retention")
- No policy-level control

### 3. Named Retention Policies (Chosen)

First-class retention policy objects that jobs reference by name. Policies define mode, duration, and scope. Jobs snapshot policy values at creation for immutability.

**Pros:**

- Reusable across jobs ("use the hipaa-6yr policy")
- Clean API ergonomics (`retention_policy: "hipaa"` vs 3+ inline params)
- Auditable - "which policy was applied to this job?"
- Foundation for access control - "only admins can use keep-forever"
- Compliance-friendly - policies have names, can be documented
- Enterprise-ready from day one

**Cons:**

- Slightly more complex than inline parameters
- Requires policy management API
- Jobs still snapshot values (for immutability), so some duplication

### 4. Crypto-Shredding Per Job

Encrypt each job's artifacts with a unique key. To "delete," destroy the key, making data cryptographically inaccessible even in backups.

**Pros:**

- Solves the backup-deletion problem completely
- Strongest compliance guarantee

**Cons:**

- Significant implementation complexity (key management, HSM/KMS integration)
- Every read/write path must handle encryption
- Overkill for a self-hosted system where the operator controls infrastructure
- Can be layered on later without changing the retention model

## Decision

Adopt **Option 3: Named Retention Policies**.

### Core Design

1. **Retention policies as first-class objects** with:
   - `name` - Human-readable identifier (e.g., "hipaa-6yr", "zero-retention")
   - `mode` - `auto_delete`, `keep`, or `none`
   - `hours` - Duration for auto_delete mode
   - `scope` - What to delete: `all` or `audio_only`
   - Realtime-specific settings for different defaults
   - Hybrid-specific settings for enhancement jobs

2. **System-defined default policies** (immutable):
   - `default` - Auto-delete after 24 hours
   - `zero-retention` - Delete immediately on completion
   - `keep` - Never auto-delete

3. **Tenant-defined custom policies** for compliance needs

4. **Jobs snapshot policy values** at creation:
   - Changing a policy doesn't affect existing jobs
   - Complete audit trail of what retention was applied
   - Jobs are self-contained

5. **Layered defaults** with operator caps:
   - System-wide defaults via environment variables
   - Per-tenant overrides via tenant settings
   - Per-job policy selection (bounded by tenant allowed policies)

6. **Background cleanup worker** - Periodic sweep of expired jobs

7. **Audit logging** - All data lifecycle events recorded

### What This Explicitly Defers

- **Crypto-shredding**: Can be layered on later for enterprise/HIPAA deployments
- **Legal holds**: Mechanism to suspend auto-deletion during litigation
- **Bulk erasure API**: GDPR-style "delete everything for this user" endpoint
- **Policy permissions**: "Only admins can use keep-forever" type restrictions

## Consequences

### Easier

- **GDPR/CCPA compliance** - Documented retention periods, deletion APIs, audit trail
- **Storage management** - Costs are bounded, old data is automatically purged
- **Privacy-sensitive deployments** - Zero-retention mode available out of the box
- **Operator control** - Defaults and caps enforced system-wide
- **API ergonomics** - Single `retention_policy` param instead of 3+ fields
- **Auditability** - Clear policy lineage for each job
- **Enterprise adoption** - Named policies are compliance-team-friendly

### Harder

- **Data model complexity** - New `retention_policies` table, policy columns on jobs
- **Operational overhead** - Cleanup worker must be running and healthy
- **Testing** - Retention behavior adds test surface area
- **Debugging** - Auto-deleted jobs can't be re-examined (mitigated by audit log)
- **Policy management** - CRUD API for policies, though minimal

### Mitigations

- Cleanup worker is lightweight (SQL query + S3 deletes) and idempotent
- Audit log preserves metadata (who, what, when) even after artifacts are gone
- `keep` mode available for debugging and development environments
- System policies cover common cases; custom policies are optional
- Retention defaults are conservative (24 hours) to avoid surprising operators
