# Dalston Implementation Plan

## Philosophy

This plan follows a **vertical slice** approach: instead of building all infrastructure before any features work, we implement thin end-to-end paths that deliver usable functionality. Each milestone produces something you can demo and test.

Within each slice, we follow a **skeleton → stub → capability** pattern:

1. **Skeleton**: Define interfaces, create file structure, wire up dependencies
2. **Stub**: Return hardcoded/mock responses, verify communication works
3. **Capability**: Incrementally add real functionality

---

## Status Overview

### Completed (8)

| # | Milestone | Completed |
|---|-----------|-----------|
| [M1](milestones/M01-hello-world.md) | Hello World | January 2026 |
| [M2](milestones/M02-real-transcription.md) | Real Transcription | 2026-01-30 |
| [M3](milestones/M03-word-timestamps.md) | Word Timestamps | 2026-01-30 |
| [M4](milestones/M04-speaker-diarization.md) | Speaker Diarization | 2026-01-30 |
| [M5](milestones/M05-export-webhooks.md) | Export & Webhooks | January 2026 |
| [M10](milestones/M10-web-console.md) | Web Console | 2026-01-30 |
| [M11](milestones/M11-api-authentication.md) | API Authentication | February 2026 |
| [M17](milestones/M17-api-key-management.md) | API Key Management | February 2026 |

### In Progress (7)

| # | Milestone | Notes |
|---|-----------|-------|
| [M6](milestones/M06-realtime-mvp.md) | Real-Time MVP | `realtime_sdk/`, `session_router/`, `engines/realtime/` |
| [M7](milestones/M07-hybrid-mode.md) | Hybrid Mode | `enhance_on_end` parameter implemented |
| [M8](milestones/M08-elevenlabs-compat.md) | ElevenLabs Compat | `gateway/api/v1/speech_to_text.py` |
| [M12](milestones/M12-python-sdk.md) | Python SDK | `sdk/dalston_sdk/` |
| [M13](milestones/M13-cli.md) | CLI | `cli/dalston_cli/` |
| [M16](milestones/M16-aws-deployment.md) | AWS Deployment | `infra/terraform/` |
| [M18](milestones/M18-unified-structured-logging.md) | Unified Structured Logging | `dalston/logging.py` |

### Not Started (5)

| # | Milestone | Goal |
|---|-----------|------|
| [M9](milestones/M09-enrichment.md) | Enrichment | Emotions, events, LLM cleanup |
| [M14](milestones/M14-model-selection.md) | Model Selection | User-selectable transcription models |
| [M15](milestones/M15-console-authentication.md) | Console Auth | Secure web console access |
| [M19](milestones/M19-distributed-tracing.md) | Distributed Tracing | OpenTelemetry spans |
| [M20](milestones/M20-metrics-dashboards.md) | Metrics & Dashboards | Prometheus + Grafana |

---

## Milestone Overview

| # | Milestone | Goal | Days | Status |
|---|-----------|------|------|--------|
| [M1](milestones/M01-hello-world.md) | Hello World | Stub end-to-end flow proves architecture | 2-3 | Completed |
| [M2](milestones/M02-real-transcription.md) | Real Transcription | Actually transcribe with faster-whisper | 3-4 | Completed |
| [M3](milestones/M03-word-timestamps.md) | Word Timestamps | WhisperX alignment for word-level timing | 2-3 | Completed |
| [M4](milestones/M04-speaker-diarization.md) | Speaker Diarization | Identify speakers with pyannote | 3-4 | Completed |
| [M5](milestones/M05-export-webhooks.md) | Export & Webhooks | SRT/VTT export, async notifications | 2 | Completed |
| [M6](milestones/M06-realtime-mvp.md) | Real-Time MVP | Stream audio → live transcripts | 5-6 | In Progress |
| [M7](milestones/M07-hybrid-mode.md) | Hybrid Mode | Real-time + batch enhancement | 2-3 | In Progress |
| [M8](milestones/M08-elevenlabs-compat.md) | ElevenLabs Compat | Drop-in API replacement | 2-3 | In Progress |
| [M9](milestones/M09-enrichment.md) | Enrichment | Emotions, events, LLM cleanup | 4-5 | Not Started |
| [M10](milestones/M10-web-console.md) | Web Console | Monitoring UI | 3-4 | Completed |
| [M11](milestones/M11-api-authentication.md) | API Authentication | Secure endpoints with API keys | 2-3 | Completed |
| [M12](milestones/M12-python-sdk.md) | Python SDK | Native SDK for Dalston features | 3-4 | In Progress |
| [M13](milestones/M13-cli.md) | CLI | Command-line interface | 2-3 | In Progress |
| [M14](milestones/M14-model-selection.md) | Model Selection | User-selectable transcription models | 2-3 | Not Started |
| [M15](milestones/M15-console-authentication.md) | Console Auth | Secure web console access | 2-3 | Not Started |

**Total: ~42-55 days (~8-11 weeks)**

---

## Operations Milestones

| # | Milestone | Goal | Days | Status |
|---|-----------|------|------|--------|
| [M16](milestones/M16-aws-deployment.md) | AWS Deployment | Single EC2 + Tailscale + S3 via Terraform | 2-3 | In Progress |

## Console Feature Milestones

| # | Milestone | Goal | Days | Status |
|---|-----------|------|------|--------|
| [M17](milestones/M17-api-key-management.md) | API Key Management | Web UI for creating/revoking API keys | 2-3 | Completed |

## Observability Milestones

| # | Milestone | Goal | Days | Status |
|---|-----------|------|------|--------|
| [M18](milestones/M18-unified-structured-logging.md) | Unified Structured Logging | Structlog everywhere, correlation IDs, JSON output | 3-4 | In Progress |
| [M19](milestones/M19-distributed-tracing.md) | Distributed Tracing | OpenTelemetry spans across all services | 3-4 | Not Started |
| [M20](milestones/M20-metrics-dashboards.md) | Metrics & Dashboards | Prometheus metrics, Grafana dashboards | 3-4 | Not Started |

---

## Timeline

```
Week 1:
├── M1: Hello World (skeleton + stubs)
└── M2: Real Transcription

Week 2:
├── M3: Word Timestamps
└── M4: Speaker Diarization

Week 3:
├── M5: Export Formats & Webhooks
└── M6: Real-Time MVP (start)

Week 4:
├── M6: Real-Time MVP (complete)
├── M7: Hybrid Mode
└── M8: ElevenLabs Compatibility

Week 5:
├── M9: Enrichment & Refinement
└── M10: Web Console (start)

Week 6:
├── M10: Web Console (complete)
├── Testing & Polish
└── Documentation
```

---

## Key Principles

1. **Vertical Slices**: Each milestone delivers testable, demonstrable functionality
2. **Skeleton First**: Wire everything up before adding real logic
3. **Stub → Real**: Replace mocks incrementally, one component at a time
4. **Test Early**: Verify each layer works before building the next
5. **Parallel Where Possible**: DAG allows enrichment tasks to run simultaneously
6. **Fail Gracefully**: Optional tasks (emotions, events) don't block the pipeline
7. **API Compatibility**: ElevenLabs layer is a thin translation, not duplication

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| GPU memory conflicts | Engine isolation via Docker |
| Model download times | Pre-download in Docker build |
| Realtime latency | Direct WebSocket, bypass queues |
| Webhook reliability | Retry with exponential backoff |
| LLM costs | Make llm-cleanup optional, batch efficiently |
| pyannote license | HF_TOKEN required, document clearly |

---

## Dependencies Between Milestones

```
M1 ──► M2 ──► M3 ──► M4 ──► M5
 │            │
 │            └──► M6 ──► M7
 │                  │
 │                  └──► M8
 │
 └──► M11 (can start early, applies to all endpoints)

M4 ──► M9

M6 ──► M10

M10 + M11 + M15 ──► M17

M18 ──► M19
M18 ──► M20 (M19 recommended but not required)
```

- **M1-M5**: Core batch pipeline (sequential)
- **M6-M8**: Real-time features (can start after M2)
- **M9**: Enrichment (needs M4 for speaker context)
- **M10**: Console (needs M6 for realtime monitoring)
- **M11**: Authentication (can start after M1, recommended before production)
- **M17**: API Key Management (needs M10 console, M11 auth, M15 console auth)
- **M18-M20**: Observability (can start immediately, M19 and M20 depend on M18)

---

## Checkpoints

Each milestone has a verification section. Key checkpoints:

| Milestone | Checkpoint |
|-----------|------------|
| M1 | `curl POST /transcriptions` returns job ID, polling returns stub transcript |
| M2 | Real audio file produces real transcript |
| M3 | Transcript includes word-level timestamps |
| M4 | Multi-speaker audio shows speaker labels |
| M5 | Download working SRT file |
| M6 | WebSocket streams live partial + final transcripts |
| M7 | Session end returns `enhancement_job_id` |
| M8 | ElevenLabs client works unchanged |
| M9 | Transcript includes emotion labels |
| M10 | Dashboard shows job queue and realtime capacity |
| M11 | Requests without valid API key return 401 |
| M15 | Console requires admin API key to access |
| M17 | API keys can be created/revoked from web console |
| M18 | `docker compose logs \| grep req_xxx` shows correlated JSON logs across all services |
| M19 | Jaeger shows end-to-end waterfall trace for a batch job |
| M20 | Grafana dashboard shows request rates, queue depths, and engine latency |
