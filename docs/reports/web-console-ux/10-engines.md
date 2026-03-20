# 10 — Engines Overview

**Route:** `/engines`
**Component:** `src/pages/Engines.tsx`
**Auth required:** Yes

## Purpose

Pipeline-stage-oriented view of all registered batch and real-time engines, their health, queue depths, and loaded models.

## Storyboard

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│  Engines                                                     │
│  Pipeline stages and engine health                           │
│                                                              │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│  │ Engines  │ │ Sessions │ │ Pipeline │ │  Issues  │       │
│  │   🖥     │ │   👥     │ │ Stages   │ │   ⚠     │       │
│  │  5/6     │ │  2/8     │ │   4/5    │ │    1     │       │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘       │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ 🖥 Pipeline                                            │  │
│  │                                                        │  │
│  │ ┌──────────────────────────────────────────────────┐   │  │
│  │ │ ▸ ● Prepare — Audio preprocessing                │   │  │
│  │ │              2 engines                            │   │  │
│  │ └──────────────────────────────────────────────────┘   │  │
│  │                                                        │  │
│  │ ┌──────────────────────────────────────────────────┐   │  │
│  │ │ ▾ ● Transcribe — Speech-to-text conversion       │   │  │
│  │ │              2 engines · 1 processing, 3 queued  │   │  │
│  │ │──────────────────────────────────────────────────│   │  │
│  │ │                                                  │   │  │
│  │ │  ┌────────────────────────────────────────────┐  │   │  │
│  │ │  │ ● stt-transcribe-faster-whisper-base       │  │   │  │
│  │ │  │   Processing · 1 processing               │  │   │  │
│  │ │  │   🟢 whisper-large-v3  🟢 whisper-base    │  │   │  │
│  │ │  └────────────────────────────────────────────┘  │   │  │
│  │ │                                                  │   │  │
│  │ │  ┌────────────────────────────────────────────┐  │   │  │
│  │ │  │ ● stt-transcribe-parakeet-rnnt · Idle      │  │   │  │
│  │ │  │   👥 2/4  ████░░░░ 50%                    │  │   │  │
│  │ │  │   🟢 parakeet-rnnt-0.6b                    │  │   │  │
│  │ │  └────────────────────────────────────────────┘  │   │  │
│  │ └──────────────────────────────────────────────────┘   │  │
│  │                                                        │  │
│  │ ┌──────────────────────────────────────────────────┐   │  │
│  │ │ ▸ ● Diarize — Speaker identification             │   │  │
│  │ │              1 engine                             │   │  │
│  │ └──────────────────────────────────────────────────┘   │  │
│  │                                                        │  │
│  │ ┌──────────────────────────────────────────────────┐   │  │
│  │ │ ▸ ⚠ Align — No engines registered                │   │  │
│  │ └──────────────────────────────────────────────────┘   │  │
│  │                                                        │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

## Sections

### 1. Summary Cards (4-column grid)
| Card | Icon | Value |
|------|------|-------|
| Engines | Server | `healthy/total` |
| Sessions | Users | `active/capacity` |
| Pipeline Stages | Layers | `active/total` |
| Issues | AlertCircle | Count of unhealthy engines |

### 2. Pipeline Accordion
Each pipeline stage (ordered: prepare → transcribe → align → diarize → pii_detect → audio_redact → merge) is a collapsible accordion section.

**Stage Header:**
- Chevron (expand/collapse)
- Aggregate status dot (green = all healthy, yellow = some warning, red = all down, gray = no engines)
- Stage label + description
- Summary: engine count, queue depth, processing count, session count

**Expanded Stage Content:**
Each engine is a clickable card linking to `/engines/:engineId`:
- Status dot + engine ID + status label (Idle/Processing/Loading/Downloading/Stale/Error/Offline)
- Batch metrics: processing count + queue depth (right side)
- RT metrics: active sessions / capacity + utilization progress bar
- Model badges: up to 3 shown with status dots (green = ready, gray = not downloaded), "+N more" badge

### Accordion State Persistence
Expanded/collapsed state is persisted in URL search params (`?expanded=transcribe,diarize`) so it survives navigation and back-button.

## Behaviour

- Data from `useEngines()` (console API) + `useModelRegistry()` (model registry API).
- Batch engines and realtime workers are merged into "unified engines" by engine_id.
- Stages derived dynamically from engine data, sorted by pipeline order.
- Loading state: 4 skeleton pulse bars.
- Error state: red banner.
