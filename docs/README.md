# Dalston Documentation

Dalston is a modular, self-hosted audio transcription server providing ElevenLabs-compatible APIs for batch and real-time transcription.

## Documentation Structure

```
docs/
├── specs/              # What the system IS (reference)
│   ├── ARCHITECTURE.md     # System overview, components, data flow
│   ├── API.md              # REST API specification
│   ├── WEBSOCKET_API.md    # WebSocket protocol reference
│   ├── DATA_MODEL.md       # Redis structures, transcript format
│   ├── ORCHESTRATOR.md     # DAG building, task scheduling
│   ├── ENGINES.md          # Engine categories, SDK reference
│   ├── SESSION_ROUTER.md   # Real-time worker management
│   ├── REALTIME.md         # Real-time architecture overview
│   ├── REALTIME_ENGINES.md # Streaming worker implementation
│   ├── DOCKER.md           # Container composition
│   └── PROJECT_STRUCTURE.md
│
├── plan/               # How we BUILD it (implementation)
│   ├── README.md           # Timeline, principles, overview
│   └── milestones/
│       ├── M01-hello-world.md
│       ├── M02-real-transcription.md
│       ├── M03-word-timestamps.md
│       ├── M04-speaker-diarization.md
│       ├── M05-export-webhooks.md
│       ├── M06-realtime-mvp.md
│       ├── M07-hybrid-mode.md
│       ├── M08-elevenlabs-compat.md
│       ├── M09-enrichment.md
│       └── M10-web-console.md
│
└── README.md           # This file
```

## Quick Links

### Specifications (Reference)
- [Architecture Overview](specs/ARCHITECTURE.md) — Start here
- [REST API](specs/API.md) — Batch transcription endpoints
- [WebSocket API](specs/WEBSOCKET_API.md) — Real-time streaming protocol

### Implementation Plan
- [Plan Overview](plan/README.md) — Timeline, principles, milestone summary
- [M1: Hello World](plan/milestones/M01-hello-world.md) — First working end-to-end flow

## Key Concepts

| Term | Definition |
|------|------------|
| **Job** | Batch request to transcribe one audio file |
| **Task** | Atomic unit of work in the batch pipeline |
| **DAG** | Directed Acyclic Graph of task dependencies |
| **Engine** | Containerized processor (batch or realtime) |
| **Session** | Real-time transcription connection |
| **Worker** | Real-time engine instance handling sessions |
