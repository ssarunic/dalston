# Dalston Documentation

Dalston is a modular, self-hosted audio transcription server providing OpenAI and ElevenLabs-compatible APIs for batch and real-time transcription.

## Documentation Structure

```text
docs/
├── README.md               # This file - navigation hub
├── GLOSSARY.md             # Terminology definitions
│
├── specs/                  # What the system IS (reference)
│   ├── ARCHITECTURE.md         # System overview, components, data flow
│   ├── OBSERVABILITY.md        # Task-level observability, stage breakdown, artifacts
│   ├── PROJECT_STRUCTURE.md    # Directory layout, packages
│   │
│   ├── batch/                  # Batch transcription specs
│   │   ├── API.md                  # REST API specification
│   │   ├── ORCHESTRATOR.md         # DAG building, task scheduling
│   │   ├── DATA_MODEL.md           # Database schemas, storage structures
│   │   ├── ENGINES.md              # Engine categories, SDK reference
│   │   └── DOCKER.md               # Container composition
│   │
│   ├── openai/                   # OpenAI-compatible API specs
│   │   └── API.md                  # OpenAI Audio API compatibility
│   │
│   ├── PII_DETECTION.md        # PII detection & audio redaction spec
│   ├── DATA_RETENTION.md       # Retention policies, cleanup worker
│   ├── AUDIT_LOG.md            # Audit logging specification
│   │
│   ├── realtime/               # Real-time transcription specs
│   │   ├── REALTIME.md             # Architecture overview
│   │   ├── WEBSOCKET_API.md        # WebSocket protocol reference
│   │   ├── SESSION_ROUTER.md       # Worker pool management
│   │   └── REALTIME_ENGINES.md     # Streaming worker implementation
│   │
│   ├── examples/               # Client implementation examples
│   │   ├── websocket-clients.md    # JS/Python client implementations
│   │   └── webhook-verification.md # Signature verification code
│   │
│   └── implementations/        # Reference patterns for building Dalston
│       ├── README.md               # When to use these references
│       ├── auth-patterns.md        # API key auth, middleware, scopes
│       ├── dag-builder.md          # Task DAG construction patterns
│       ├── enrichment-engines.md   # Emotion, events, LLM engines
│       └── console-api.md          # Console API aggregation
│
├── decisions/              # Architecture Decision Records (ADRs)
│   ├── README.md               # ADR index and template
│   ├── ADR-001-storage-architecture.md
│   ├── ADR-002-engine-isolation.md
│   ├── ADR-003-two-level-queues.md
│   ├── ADR-004-task-level-observability.md
│   └── ADR-005-unified-logging.md
│
└── plan/                   # How we BUILD it (implementation)
    ├── README.md               # Timeline, principles, overview
    └── milestones/
        ├── M01-hello-world.md
        ├── M02-real-transcription.md
        ├── M03-word-timestamps.md
        ├── M04-speaker-diarization.md
        ├── M05-export-webhooks.md
        ├── M06-realtime-mvp.md
        ├── M07-hybrid-mode.md
        ├── M08-elevenlabs-compat.md
        ├── M09-enrichment.md
        ├── M10-web-console.md
        ├── M11-api-authentication.md
        ├── ...
        ├── M18-unified-structured-logging.md
        ├── M19-distributed-tracing.md
        └── M20-metrics-dashboards.md
```

## Quick Links

### Specifications (Reference)

- [Architecture Overview](specs/ARCHITECTURE.md) — Start here
- [REST API](specs/batch/API.md) — Batch transcription endpoints (Dalston native + ElevenLabs compatible)
- [OpenAI-Compatible API](specs/openai/API.md) — OpenAI Audio API compatibility layer
- [WebSocket API](specs/realtime/WEBSOCKET_API.md) — Real-time streaming protocol
- [PII Detection & Redaction](specs/PII_DETECTION.md) — PII detection and audio redaction
- [Data Retention](specs/DATA_RETENTION.md) — Retention policies and cleanup
- [Audit Log](specs/AUDIT_LOG.md) — Audit logging specification
- [Task-Level Observability](specs/OBSERVABILITY.md) — Stage breakdown and artifact inspection
- [Glossary](GLOSSARY.md) — Terminology definitions

### Architecture Decisions

- [ADR Index](decisions/README.md) — Why we made key technical choices

### Implementation Reference

- [Implementation Patterns](specs/implementations/README.md) — Non-obvious patterns for building Dalston

### Implementation Plan

- [Plan Overview](plan/README.md) — Timeline, principles, milestone summary
- [M1: Hello World](plan/milestones/M01-hello-world.md) — First working end-to-end flow

## Key Concepts

| Term | Definition |
| --- | --- |
| **Job** | Batch request to transcribe one audio file |
| **Task** | Atomic unit of work in the batch pipeline |
| **DAG** | Directed Acyclic Graph of task dependencies |
| **Engine** | Containerized processor (batch or realtime) |
| **Session** | Real-time transcription connection |
| **Worker** | Real-time engine instance handling sessions |

See [GLOSSARY.md](GLOSSARY.md) for complete terminology.
