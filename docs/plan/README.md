# Dalston Implementation Plan

## Philosophy

This plan follows a **vertical slice** approach: instead of building all infrastructure before any features work, we implement thin end-to-end paths that deliver usable functionality. Each milestone produces something you can demo and test.

Within each slice, we follow a **skeleton → stub → capability** pattern:

1. **Skeleton**: Define interfaces, create file structure, wire up dependencies
2. **Stub**: Return hardcoded/mock responses, verify communication works
3. **Capability**: Incrementally add real functionality

---

## Status Overview

### Completed (22)

| # | Milestone | Completed |
|---|-----------|-----------|
| [M1](milestones/M01-hello-world.md) | Hello World | January 2026 |
| [M2](milestones/M02-real-transcription.md) | Real Transcription | 2026-01-30 |
| [M3](milestones/M03-word-timestamps.md) | Word Timestamps | 2026-01-30 |
| [M4](milestones/M04-speaker-diarization.md) | Speaker Diarization | 2026-01-30 |
| [M5](milestones/M05-export-webhooks.md) | Export & Webhooks | January 2026 |
| [M10](milestones/M10-web-console.md) | Web Console | 2026-01-30 |
| [M11](milestones/M11-api-authentication.md) | API Authentication | February 2026 |
| [M15](milestones/M15-console-authentication.md) | Console Auth | February 2026 |
| [M17](milestones/M17-api-key-management.md) | API Key Management | February 2026 |
| [M21](milestones/M21-admin-webhooks.md) | Admin Webhooks | February 2026 |
| [M25](milestones/M25-data-retention.md) | Data Retention & Audit | February 2026 |
| [M26](milestones/M26-pii-detection-redaction.md) | PII Detection & Audio Redaction | February 2026 |
| [M19](milestones/M19-distributed-tracing.md) | Distributed Tracing | 2026-02-11 |
| [M20](milestones/M20-metrics-dashboards.md) | Metrics & Dashboards | 2026-02-12 |
| [M38](milestones/M38-openai-compat.md) | OpenAI Compatibility | 2026-03-01 |
| [M36](milestones/M36-runtime-model-management.md) | Runtime Model Management | March 2026 |
| [M39](milestones/M39-model-cache-ttl.md) | Model Cache & TTL | March 2026 |
| [M40](milestones/M40-model-registry.md) | Model Registry & HF Integration | March 2026 |
| [M42](milestones/M42-console-model-management.md) | Console Model Management | March 2026 |
| [M47](milestones/M47-sql-layer-separation.md) | SQL Layer Separation | March 2026 |
| [M53](milestones/M53-realtime-latency-budget-clean-cut.md) | Realtime Latency Budget and Explicit Backpressure (Clean-Cut) | 2026-03-05 |
| [M54](milestones/M54-event-dlq-poison-pill-isolation-clean-cut.md) | Event DLQ and Poison-Pill Isolation (Clean-Cut) | 2026-03-05 |

### In Progress (8)

| # | Milestone | Notes |
|---|-----------|-------|
| [M6](milestones/M06-realtime-mvp.md) | Real-Time MVP | `realtime_sdk/`, `session_router/`, `engines/realtime/` |
| [M7](milestones/M07-hybrid-mode.md) | Hybrid Mode | `enhance_on_end` parameter implemented |
| [M8](milestones/M08-elevenlabs-compat.md) | ElevenLabs Compat | `gateway/api/v1/speech_to_text.py` |
| [M12](milestones/M12-python-sdk.md) | Python SDK | `sdk/dalston_sdk/` |
| [M13](milestones/M13-cli.md) | CLI | `cli/dalston_cli/` |
| [M16](milestones/M16-aws-deployment.md) | AWS Deployment | `infra/terraform/` |
| [M18](milestones/M18-unified-structured-logging.md) | Unified Structured Logging | `dalston/logging.py` |
| [M24](milestones/M24-realtime-session-persistence.md) | Realtime Session Persistence | Audio/transcript S3 storage working; session resume pending |

### Not Started (4)

| # | Milestone | Goal |
|---|-----------|------|
| [M9](milestones/M09-enrichment.md) | Enrichment | Emotions, events, LLM cleanup |
| [M55](milestones/M55-non-transcribe-runtime-model-management-clean-cut.md) | Non-Transcribe Runtime Model Management (Clean-Cut) | Runtime model selection and registry-backed lifecycle for diarize, align, and PII stages |
| [M56](milestones/M56-lite-mode-infra-backends-clean-cut.md) | Lite Mode Infra Backends (Clean-Cut) | Mode-aware backend abstraction for DB/queue/storage with SQLite, in-memory queue, and local filesystem storage |
| [M57](milestones/M57-ghost-server-zero-config-cli-bootstrap.md) | Ghost Server + Zero-Config CLI Bootstrap (Clean-Cut) | One-command transcribe UX with automatic local server boot and model auto-ensure |

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
| [M14](milestones/M14-model-selection.md) | Model Selection | User-selectable transcription models | 2-3 | Superseded by M36/M40 |
| [M15](milestones/M15-console-authentication.md) | Console Auth | Secure web console access | 2-3 | Completed |

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
| [M27](milestones/M27-console-ux-improvements.md) | Console UX Improvements | Slide-over panels, audio player, search, responsive design | 8-10 | Not Started |
| [M35](milestones/M35-settings-page.md) | Settings Page | Admin console for viewing/editing system config without redeploying | 4-5 | Not Started |

## Observability Milestones

| # | Milestone | Goal | Days | Status |
|---|-----------|------|------|--------|
| [M18](milestones/M18-unified-structured-logging.md) | Unified Structured Logging | Structlog everywhere, correlation IDs, JSON output | 3-4 | In Progress |
| [M19](milestones/M19-distributed-tracing.md) | Distributed Tracing | OpenTelemetry spans across all services | 3-4 | Completed |
| [M20](milestones/M20-metrics-dashboards.md) | Metrics & Dashboards | Prometheus metrics, Grafana dashboards | 3-4 | Completed |

## API Feature Milestones

| # | Milestone | Goal | Days | Status |
|---|-----------|------|------|--------|
| [M21](milestones/M21-admin-webhooks.md) | Admin Webhooks | Admin-registered webhook endpoints with persistent delivery | 3-4 | Completed |
| [M38](milestones/M38-openai-compat.md) | OpenAI Compatibility | Drop-in OpenAI Audio API replacement | 3-4 | Completed |

## Engine Milestones

| # | Milestone | Goal | Days | Status |
|---|-----------|------|------|--------|
| [M22](milestones/M22-parakeet-engine.md) | Parakeet Engine | NVIDIA Parakeet batch + real-time transcription engines | 4-5 | Complete |

## Realtime Feature Milestones

| # | Milestone | Goal | Days | Status |
|---|-----------|------|------|--------|
| [M24](milestones/M24-realtime-session-persistence.md) | Realtime Session Persistence | Session DB, audio/transcript S3 storage, console visibility | 3-4 | Nearly Complete |
| [M53](milestones/M53-realtime-latency-budget-clean-cut.md) | Realtime Latency Budget and Explicit Backpressure (Clean-Cut) | Enforce explicit lag budget with `processing_lag` warning and lag-exceeded close semantics | 3-5 | Complete |

## Data Management Milestones

| # | Milestone | Goal | Days | Status |
|---|-----------|------|------|--------|
| [M25](milestones/M25-data-retention.md) | Data Retention & Audit | Named retention policies, cleanup worker, audit logging | 5-6 | Completed |

## Compliance Milestones

| # | Milestone | Goal | Days | Status |
|---|-----------|------|------|--------|
| [M26](milestones/M26-pii-detection-redaction.md) | PII Detection & Audio Redaction | Detect PII, redact text and audio | 8-10 | Completed |

## Engine Infrastructure Milestones

| # | Milestone | Goal | Days | Status |
|---|-----------|------|------|--------|
| [M28](milestones/M28-batch-engine-registry.md) | Batch Engine Registry | Fail-fast when engines unavailable | 2-3 | Complete |
| [M29](milestones/M29-engine-catalog-capabilities.md) | Engine Catalog & Capabilities | Validate job requirements against engine capabilities | 2-3 | Complete |
| [M30](milestones/M30-engine-metadata-evolution.md) | Engine Metadata Evolution | Single source of truth for engine metadata; discovery API | 8-10 | Complete |
| [M31](milestones/M31-capability-driven-routing.md) | Capability-Driven Routing | Route jobs based on engine capabilities | 2-3 | Complete |
| [M32](milestones/M32-engine-variant-structure.md) | Engine Variant Structure | Model sizes as separate deployable engines | 1.5-2 | Superseded by M36 |
| [M47](milestones/M47-sql-layer-separation.md) | SQL Layer Separation | Move SQL out of handlers into services and establish service-level data access boundaries | 1-2 | Complete |
| [M51](milestones/M51-engine-runtime-context-refactor.md) | Engine Runtime Context Refactor | Stateless URI-free engine contract with runner-side artifact materialization | 12-16 | Core Complete (engine migration → M52) |
| [M52](milestones/M52-engine-sdk-local-runner-dx-clean-cut.md) | Engine SDK Local Runner DX (Clean-Cut) | File-based local runner command and no-compat cleanup before remaining stage refactors | 5-7 | Complete |
| [M54](milestones/M54-event-dlq-poison-pill-isolation-clean-cut.md) | Event DLQ and Poison-Pill Isolation (Clean-Cut) | Delivery-count retry ceiling + DLQ quarantine for durable orchestrator events; remove infinite replay legacy behavior | 3-5 | Complete |

## Model Management Milestones

| # | Milestone | Goal | Days | Status |
|---|-----------|------|------|--------|
| [M36](milestones/M36-runtime-model-management.md) | Runtime Model Management | Engines load any model at runtime; two-catalog architecture | 4-5 | Complete |
| [M39](milestones/M39-model-cache-ttl.md) | Model Cache & TTL | Unified model cache with TTL-based eviction | 2-3 | Complete |
| [M40](milestones/M40-model-registry.md) | Model Registry & HF Integration | PostgreSQL model registry, HF auto-routing, download workflow | 3-4 | Complete |
| [M41](milestones/M41-new-engine-types.md) | New Engine Types | Parakeet ONNX, HF-ASR, vLLM-ASR engine containers | 5-7 | Planned |
| [M42](milestones/M42-console-model-management.md) | Console Model Management | Web UI for model registry, download, and selection | 5-7 | Complete |
| [M46](milestones/M46-model-registry-as-source-of-truth.md) | Model Registry as Source of Truth | DB as single source, auto-seeding, user enrichment API | 3-4 | Planned |
| [M55](milestones/M55-non-transcribe-runtime-model-management-clean-cut.md) | Non-Transcribe Runtime Model Management (Clean-Cut) | Runtime model selection and registry lifecycle for diarize, align, and PII stages | 8-12 | Planned |

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
 │            └──► M6 ──► M24 ──► M7
 │                  │
 │                  └──► M8
 │
 └──► M11 (can start early, applies to all endpoints)

M4 ──► M9

M6 ──► M10

M6 + M8 ──► M38 (OpenAI compatibility layer)

M10 + M11 + M15 ──► M17

M18 ──► M19
M18 ──► M20 (M19 recommended but not required)

M5 + M11 ──► M21

M2 + M6 + M14 ──► M22

M11 + M21 ──► M25

M6 ──► M24 (realtime session persistence, prerequisite for M7)

M3 + M4 + M25 ──► M26

M28 ──► M29 ──► M30 ──► M31 ──► M36 (runtime model management)
                  │                │
                  └──► M32         └──► M39 ──► M40 ──► M41
                                             │
                                             ├──► M42 (console model management, also needs M10)
                                             │
                                             └──► M46 (model registry as source of truth)

M43 + M48 + M49 ──► M51 (engine runtime context refactor) ──► M52 (local runner DX clean-cut)

M6 + M8 ──► M53 (realtime latency budget + explicit backpressure)
M33 ──► M54 (event DLQ + poison-pill isolation)
M36 + M40 + M46 ──► M55 (non-transcribe runtime model management)
M47 + M52 ──► M56 (lite mode infra backends)
M56 + M13 + M40 ──► M57 (ghost server + zero-config CLI bootstrap)

M10 + M11 + M15 ──► M35
```

- **M1-M5**: Core batch pipeline (sequential)
- **M6-M8**: Real-time features (can start after M2)
- **M9**: Enrichment (needs M4 for speaker context)
- **M10**: Console (needs M6 for realtime monitoring)
- **M11**: Authentication (can start after M1, recommended before production)
- **M17**: API Key Management (needs M10 console, M11 auth, M15 console auth)
- **M18-M20**: Observability (can start immediately, M19 and M20 depend on M18)
- **M21**: Admin Webhooks (needs M5 webhooks, M11 auth)
- **M22**: Parakeet Engine (needs M2 batch, M6 real-time, M14 model selection)
- **M24**: Realtime Session Persistence (needs M6 real-time, prerequisite for M7 hybrid)
- **M53**: Realtime Latency Budget and Explicit Backpressure (needs M6 realtime path and M8 translation endpoints)
- **M54**: Durable orchestrator event reliability cutover with max-delivery DLQ policy, malformed-event quarantine, and legacy infinite-replay cleanup
- **M56**: Mode-aware infra abstraction (`lite` vs `distributed`) for DB, queue, and storage, with one validated lite batch path and no distributed regressions
- **M57**: Zero-config CLI bootstrap (`dalston transcribe`) with automatic local server startup and default-model auto-ensure for first-run success
- **M47**: SQL layer separation complete; M56 builds on those service boundaries to swap backend implementations with minimal handler impact
- **M25**: Data Retention & Audit (needs M11 auth, M21 webhooks for purge events)
- **M26**: PII Detection & Audio Redaction (needs M3 word timestamps, M4 diarization, M25 retention)
- **M28-M32**: Engine infrastructure (M28 registry → M29 capabilities → M30 metadata → M31 routing → M32 variants)
- **M51**: Stateless engine contract + artifact materialization refactor (batch + realtime side-effect boundaries, local runner)
- **M52**: Local runner DX clean-cut (`audio + config.json -> output.json`), then use that harness for diarize/align/PII refactor sweep and remove remaining compatibility bridges
  - Readiness note: `docs/reports/M52-local-runner-readiness.md` (align + diarize local-run validated; pii-detect dependency gap captured)
- **M35**: Settings Page (needs M10 console, M11 auth, M15 console auth)
- **M36**: Runtime Model Management (needs M31 routing; enables dynamic model loading)
- **M39-M46**: Model management (M36 → M39 cache TTL → M40 registry → M41 new engines, M42 console, M46 DB source of truth)
- **M55**: Non-transcribe runtime model management (needs M36 runtime loading, M40 registry, M46 DB source of truth; extends model pluggability to diarize/align/PII stages)
- **M42**: Console Model Management (needs M40 registry APIs, M10 console)

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
| M21 | Registered webhook endpoints receive notifications without per-job URL |
| M22 | English audio transcribed with Parakeet; real-time streaming with sub-100ms latency |
| M24 | Realtime sessions stored in DB; audio/transcript saved to S3; sessions visible in console |
| M25 | Jobs auto-purged after retention period; audit log shows full lifecycle |
| M26 | PII detected in transcript; redacted audio produced with silence over PII spans |
| M28 | Job fails immediately with clear error when engine not running |
| M29 | Job fails with capability mismatch error (e.g., unsupported language) |
| M30 | `GET /v1/engines` returns engine list; `GET /v1/capabilities` returns aggregate capabilities |
| M32 | Catalog shows `whisper-base`, `whisper-large-v3`, `whisper-large-v3-turbo` as separate engines |
| M35 | Settings page shows current values; admin can change rate limits and see effect immediately |
| M36 | Engines load models on-demand; `GET /v1/engines` shows `loaded_model` per engine |
| M39 | Model files evicted after TTL; cache stays within size limits |
| M40 | `POST /v1/models/{id}/pull` downloads to S3; HF models auto-resolve to runtime |
| M42 | Models page shows registry with download/remove actions; Add from HF dialog works |
| M46 | Models auto-seeded on startup; PATCH updates metadata; user edits preserved across restarts |
| M51 | Batch engines are URI-free/stateless (`process(input, ctx)`), orchestrator passes artifact refs, runner materializes/persists artifacts, and local runner works without Redis/S3 |
| M52 | Developer can run `python -m dalston.engine_sdk.local_runner run` with local `audio + config.json` and get canonical `output.json` without Redis/S3; legacy compatibility bridges removed |
| M54 | Poison or malformed durable events are quarantined in `dalston:events:dlq` after policy thresholds; main stream entries are ACKed and healthy events continue processing |
| M55 | Diarize, align, and PII stages accept `runtime_model_id`; models registered in registry with explicit stage; per-stage model selection works in standard and per-channel DAGs |
| M56 | `DALSTON_MODE=lite` runs scoped batch transcription without Postgres/Redis/MinIO using SQLite + in-memory queue + localfs artifacts; distributed mode remains stable |
| M57 | `dalston transcribe <file>` auto-starts local server when needed, auto-ensures default model, and returns transcript in one command |
