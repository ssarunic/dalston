# ADR-004: Data Retention Strategy

## Status

Accepted

## Context

Dalston processes sensitive audio recordings and produces transcripts that may contain personal, medical, or legally privileged information. Currently, all job artifacts (uploaded audio, intermediate processing files, final transcripts) persist indefinitely in S3 with no automated cleanup, no configurable retention, and no audit trail of data access or deletion.

This creates several problems:

- **Storage costs** grow unbounded as jobs accumulate
- **Privacy compliance** (GDPR, HIPAA, CCPA) requires data minimization, documented retention periods, and the ability to honor erasure requests
- **Security posture** is weakened by retaining sensitive data longer than necessary
- **Operator control** is limited — self-hosted deployments have no mechanism to enforce data lifecycle policies

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

- Simplest implementation — one cron job, one config value
- Easy to reason about

**Cons:**

- No per-job flexibility (a user can't keep some jobs and purge others)
- No selective deletion (can't delete audio but keep transcript)
- Doesn't support zero-retention or indefinite-keep use cases
- Not aligned with how any major provider does it

### 2. Per-Job Retention with Operator Defaults (Chosen)

Each job carries its own retention policy (mode + duration), with system-wide and per-tenant defaults. Operators set bounds; API users choose within those bounds.

**Pros:**

- Matches industry standard (AssemblyAI, Azure, ElevenLabs all offer per-request retention control)
- Flexible enough for diverse compliance requirements
- Operator retains control via maximum caps
- Supports all common patterns: zero-retention, short-term, keep-indefinitely

**Cons:**

- More complex than a single global setting
- Requires a background cleanup worker
- Per-job retention fields add to the data model

### 3. Crypto-Shredding Per Job

Encrypt each job's artifacts with a unique key. To "delete," destroy the key, making data cryptographically inaccessible even in backups.

**Pros:**

- Solves the backup-deletion problem completely
- Strongest compliance guarantee
- Elegant for multi-stage pipelines where artifacts are scattered

**Cons:**

- Significant implementation complexity (key management, HSM/KMS integration)
- Every read/write path must handle encryption
- Key storage becomes a critical dependency
- Overkill for a self-hosted system where the operator controls infrastructure

## Decision

Adopt **Option 2: Per-Job Retention with Operator Defaults**.

### Core Design

1. **Three retention modes** per job:
   - `auto_delete` — Artifacts purged after a configurable duration (default)
   - `keep` — Nothing auto-deleted; user manages lifecycle
   - `none` — Zero retention; artifacts deleted immediately after job completion

2. **Layered defaults** with operator caps:
   - System-wide defaults via environment variables
   - Per-tenant overrides via tenant settings
   - Per-job overrides at submission time (bounded by operator maximum)

3. **Selective deletion** — Option to delete audio but retain the transcript (small, useful, low-risk)

4. **Background cleanup worker** — Periodic sweep of expired jobs, integrated into the orchestrator

5. **Audit logging** — All data lifecycle events (creation, access, deletion) recorded in an append-only log

### What This Explicitly Defers

- **Crypto-shredding**: Can be layered on later for enterprise/HIPAA deployments without changing the retention model
- **Legal holds**: Mechanism to suspend auto-deletion for specific jobs during litigation
- **Bulk erasure API**: GDPR-style "delete everything for this user" endpoint
- **Backup-aware deletion**: Requires crypto-shredding; for now, operators manage backup rotation

## Consequences

### Easier

- GDPR/CCPA compliance — documented retention periods, deletion APIs, audit trail
- Storage management — costs are bounded, old data is automatically purged
- Privacy-sensitive deployments — zero-retention mode available out of the box
- Operator control — defaults and caps enforced system-wide

### Harder

- Data model complexity — jobs table gains retention columns
- Operational overhead — cleanup worker must be running and healthy
- Testing — retention behavior adds test surface area
- Debugging — auto-deleted jobs can't be re-examined (mitigated by audit log)

### Mitigations

- Cleanup worker is lightweight (SQL query + S3 deletes) and idempotent
- Audit log preserves metadata (who, what, when) even after artifacts are gone
- `keep` mode available for debugging and development environments
- Retention defaults are conservative (24 hours) to avoid surprising operators
