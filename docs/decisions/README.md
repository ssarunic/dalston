# Architecture Decision Records

This directory contains Architecture Decision Records (ADRs) documenting significant technical decisions made in the Dalston project.

## Index

| ADR | Title | Status |
| --- | --- | --- |
| [ADR-001](ADR-001-storage-architecture.md) | Storage Architecture (PostgreSQL + Redis + S3) | Accepted |
| [ADR-002](ADR-002-engine-isolation.md) | Engine Isolation via Docker Containers | Accepted |
| [ADR-003](ADR-003-two-level-queues.md) | Two-Level Queue Model (Jobs → Tasks) | Accepted |
| [ADR-004](ADR-004-task-level-observability.md) | Two-Tier Task Observability (Stage Breakdown + Artifact Inspection) | Accepted |
| [ADR-005](ADR-005-unified-logging.md) | Unified Logging and Observability | Proposed |
| [ADR-006](ADR-006-api-key-storage-migration.md) | Migrate API Key Storage from Redis to PostgreSQL | Accepted |
| [ADR-007](ADR-007-admin-webhook-design.md) | Admin Webhook Design (Storage, Signing, Delivery) | Proposed |
| [ADR-008](ADR-008-data-retention-strategy.md) | Data Retention Strategy (Named Policies, Audit Logging) | Proposed |
| [ADR-009](ADR-009-pii-detection-architecture.md) | PII Detection & Audio Redaction Architecture | Accepted |

## ADR Template

When creating a new ADR, use this template:

```markdown
# ADR-NNN: Title

## Status

Accepted | Proposed | Deprecated | Superseded by [ADR-NNN](link)

## Context

What is the issue that we're seeing that is motivating this decision or change?

## Options Considered

1. **Option A** — Description, pros, cons
2. **Option B** — Description, pros, cons
3. **Option C** — Description, pros, cons

## Decision

What is the change that we're proposing and/or doing?

## Consequences

What becomes easier or more difficult to do because of this change?
```

## When to Write an ADR

Write an ADR when:

- Choosing between multiple viable technical approaches
- Making decisions that are difficult to reverse
- Establishing patterns that will be repeated throughout the codebase
- Making tradeoffs that future maintainers should understand

ADRs are not needed for:

- Implementation details that follow established patterns
- Obvious choices with no meaningful alternatives
- Decisions that can be easily changed later
