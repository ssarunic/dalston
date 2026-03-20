# 11 — Engine Detail

**Route:** `/engines/:engineId`
**Component:** `src/pages/EngineDetail.tsx`
**Auth required:** Yes

## Purpose

Detailed view of a single engine: status, batch queue metrics, realtime session utilization, available models, and engine capabilities.

## Storyboard

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│  [←] stt-transcribe-faster-whisper-base  ● Running          │
│      Stage: transcribe · Version: 1.2.0                      │
│                                                              │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│  │  Stage   │ │Processing│ │  Queue   │ │ Max      │       │
│  │ 🔲       │ │ ⚡       │ │ Depth 🕐 │ │ Concurr. │       │
│  │transcribe│ │    1     │ │    3     │ │    4     │       │
│  │          │ │active tsk│ │ waiting  │ │          │       │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘       │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ 👥 Session Utilization                                 │  │
│  │                                                        │  │
│  │  2 active / 8 capacity (2 workers)            25%      │  │
│  │  ████████░░░░░░░░░░░░░░░░░░░░░░                       │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ 📦 Available Models                                    │  │
│  │   2/3 word timestamps · 1/3 streaming                  │  │
│  │                                                        │  │
│  │  ┌─────────────────────┐ ┌─────────────────────┐      │  │
│  │  │ Whisper Large V3    │ │ Whisper Base         │      │  │
│  │  │ Systran/faster-...  │ │ Systran/faster-...   │      │  │
│  │  │ 🟢 Ready            │ │ 🟢 Ready             │      │  │
│  │  │ [word timestamps]   │ │ [word timestamps]    │      │  │
│  │  │ [streaming] [2.9GB] │ │ [0.4GB]              │      │  │
│  │  └─────────────────────┘ └─────────────────────┘      │  │
│  │  ┌─────────────────────┐                               │  │
│  │  │ Whisper Tiny         │                               │  │
│  │  │ Systran/faster-...   │                               │  │
│  │  │ ⚪ Not Downloaded     │                               │  │
│  │  │ [GPU only]           │                               │  │
│  │  └─────────────────────┘                               │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ Engine Details                                         │  │
│  │                                                        │  │
│  │  Engine ID  │ stt-transcribe-faster-whisper-base       │  │
│  │  Name       │ Faster Whisper Base                      │  │
│  │  Version    │ 1.2.0                                    │  │
│  │  Stage      │ transcribe                               │  │
│  │  Status     │ Processing                               │  │
│  │  Max Audio  │ 2h                                       │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

## Sections

### 1. Header
- Back button → `/engines`
- Engine ID as title (bold, truncated on mobile)
- StatusDot + StatusBadge (Running/Available/Unhealthy)
- Stage label + version

### 2. Quick Stats (4-column grid)
- Stage, Processing count, Queue depth, Max concurrency

### 3. Session Utilization (conditional, only for RT-capable engines)
- Progress bar showing active/capacity
- Worker count in parentheses
- Percentage label
- Color: green <80%, yellow >80%

### 4. Available Models (2-column grid)
- Cards for each model registered to this engine
- Model name (or ID), loaded_model_id subtitle
- Status dot + label (Ready / Not Downloaded)
- Capability badges: "word timestamps", "streaming", size in GB, "GPU only"
- Green border for ready models
- Header shows aggregate: "X/Y word timestamps · X/Y streaming"

### 5. Engine Details (key-value list)
- ID, Name, Version, Stage, Status, Max Audio Duration
- Only shown when discovery API returns engine info

## Behaviour

- Dual data sources: discovery API (`/engines`) for capabilities + console API for batch queue stats.
- Realtime workers matched by `engine_id`.
- Loading state: skeleton bars.
- Not found: centered AlertCircle + message + hint to check if engine is running.
