# 02 — Dashboard

**Route:** `/`
**Component:** `src/pages/Dashboard.tsx`
**Auth required:** Yes

## Purpose

System overview showing health, key metrics, recent activity, and capabilities at a glance.

## Storyboard

```
┌──────────┬──────────────────────────────────────────────────┐
│          │                                                  │
│ DALSTON  │  Dashboard                                       │
│ Console  │  System overview and recent activity             │
│          │                                                  │
│ ■ Dash   │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌─────────┐
│ □ Jobs   │  │ System   │ │ Running  │ │ Realtime │ │Completed│
│ □ RT     │  │ Status   │ │ Jobs     │ │ Sessions │ │ Today   │
│ □ Eng    │  │          │ │          │ │          │ │         │
│ □ Infra  │  │ Online   │ │    3     │ │   2/8    │ │   47    │
│ □ Models │  │ v0.9.2   │ │ 5 queued │ │ 4 wkrs   │ │ 0 fail  │
│ □ Keys   │  └──────────┘ └──────────┘ └──────────┘ └─────────┘
│ □ Webhks │                                                  │
│ □ Audit  │  ┌────────────────────────────────────────────┐  │
│ □ Settngs│  │ Key Metrics                                │  │
│          │  │  [P50 latency] [P99 latency] [Throughput]  │  │
│ ─────────│  │  [Error rate]  [Queue depth]               │  │
│ dsk_1a2b.│  └────────────────────────────────────────────┘  │
│ ⏏ Logout │                                                  │
│          │  ┌───────────────────┐ ┌───────────────────┐     │
│          │  │ Recent Batch Jobs │ │ Recent RT Sessions│     │
│          │  │ View all →        │ │ View all →        │     │
│          │  │                   │ │                   │     │
│          │  │ ● ab3c... 5m ago  │ │ ● f7e2... 2m ago  │     │
│          │  │ ● 9d1f... 12m ago │ │ ● a1b4... 8m ago  │     │
│          │  │ ● e5a2... 1h ago  │ │ ● c3d9... 15m ago │     │
│          │  └───────────────────┘ └───────────────────┘     │
│          │                                                  │
│          │  ┌────────────────────────────────────────────┐  │
│          │  │ System Capabilities                        │  │
│          │  │  Batch: ✓  Realtime: ✓  Diarize: ✓        │  │
│          │  │  PII: ✓   Align: ✓     Redact: ✗          │  │
│          │  └────────────────────────────────────────────┘  │
└──────────┴──────────────────────────────────────────────────┘
```

## Sections

### 1. Status Cards (4-column grid)
| Card | Icon | Value | Subtitle |
|------|------|-------|----------|
| System Status | Activity | "Online" / "Offline" | Version string |
| Running Jobs | Cpu | Count | "X queued" |
| Realtime Sessions | Radio | "active/capacity" | "X workers" |
| Completed Today | CheckCircle | Count | "X failed" or "No failures" |

### 2. Key Metrics Panel (`<MetricsPanel>`)
Displays server-side metrics (latency percentiles, throughput, error rates) fetched from `/console/metrics`. Shown as a grid of metric cards with sparkline-style values.

### 3. Recent Activity (2-column grid)
- **Recent Batch Jobs:** Last 5 jobs from `/v1/audio/transcriptions`. Each row: StatusBadge + truncated job ID + time ago. Click → Job Detail.
- **Recent RT Sessions:** Last 5 sessions from `/console/realtime/sessions`. Each row: StatusBadge + truncated session ID + time ago. Click → Session Detail.
- Both cards have a "View all →" link in the header.

### 4. System Capabilities Card (`<CapabilitiesCard>`)
Fetches `/console/capabilities` and displays which pipeline stages are available (batch transcription, realtime, diarization, alignment, PII detection, audio redaction).

## Behaviour

- All data fetched on mount via React Query hooks (`useDashboard`, `useMetrics`, `useRealtimeSessions`).
- `refetchOnWindowFocus` ensures live data when user returns to tab.
- Handles gracefully when backend is offline (status shows "Offline", metrics show "-").
- Time-ago values (`formatTimeAgo`) update only on data refetch, not live.

## Responsive

- Desktop: 4-col stat cards, 2-col recent activity grid.
- Tablet: 2-col stat cards, stacked activity.
- Mobile: Single column throughout.
