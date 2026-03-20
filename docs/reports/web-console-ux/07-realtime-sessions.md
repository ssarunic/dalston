# 07 — Real-time Sessions List

**Route:** `/realtime`
**Component:** `src/pages/RealtimeSessions.tsx`
**Auth required:** Yes

## Purpose

Monitor real-time transcription infrastructure status and browse session history.

## Storyboard

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│  Real-time                              [🎤 New Session]     │
│  Live transcription sessions                                 │
│                                                              │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐   │
│  │ Status   │ │ Active   │ │ Workers  │ │ Capacity     │   │
│  │          │ │ Sessions │ │          │ │ Overview     │   │
│  │ ● Ready  │ │ 2 / 8   │ │  4 / 4   │ │ ████░░ 25%  │   │
│  │          │ │ 6 avail. │ │  ready   │ │ 2/8 used    │   │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────┘   │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ ✓ System healthy and accepting sessions                │  │
│  │   4/4 workers ready. Start a session via the API or    │  │
│  │   click "New Session" above.                           │  │
│  │                                                        │  │
│  │   [Check Engine Health]  [Why this state?]             │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ Session History    [Status ▾] [Sort ▾] [Page size ▾]  │  │
│  │────────────────────────────────────────────────────────│  │
│  │ ID        │Status  │Model    │Duration│Segs│Created│  │  │
│  │───────────┼────────┼─────────┼────────┼────┼───────┼──│  │
│  │ f7e2a1b.. │●active │parakeet │  12m   │ 45 │ 3m ago│  │  │
│  │ a1b4c3d.. │●compl  │whisper  │  5m 2s │ 28 │ 1h ago│🗑│  │
│  │ c3d9e5f.. │●error  │parakeet │  0s    │  0 │ 2h ago│🗑│  │
│  │ b2c4d6e.. │●compl  │whisper  │ 22m 15s│ 94 │ 3h ago│🗑│  │
│  │───────────┴────────┴─────────┴────────┴────┴───────┴──│  │
│  │ Showing 4 sessions                    [Load More]      │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

## Sections

### 1. Header
- Title + subtitle on left.
- "New Session" button (microphone icon) → navigates to `/realtime/live`.

### 2. Status Cards (4-column grid)
| Card | Content |
|------|---------|
| Status | Colored dot (green/yellow/red) + status text (Ready/At capacity/Unavailable) |
| Active Sessions | `active/capacity` format + available count |
| Workers | `ready/total` format + "ready" label |
| Capacity Overview | Used count + visual progress bar |

### 3. Status Guidance Card
Context-aware guidance card that changes based on system status:

| System State | Card Style | Message | Actions |
|-------------|------------|---------|---------|
| Healthy | Default | "System healthy and accepting sessions" | [Check Engine Health] |
| At Capacity | Amber border | "All sessions are in use" | [Check Engine Health] |
| Unavailable (no workers) | Red border | "No workers running" | [Check Engine Health] [Start Worker] |
| Unavailable (unhealthy) | Red border | "Workers unhealthy" | [Check Engine Health] |

"Why this state?" expands detailed explanation panel with Docker start commands for self-hosted setups.

### 4. Session History Table
Same table pattern as Batch Jobs:
- **Columns:** ID (mono, truncated), Status, Model (+ engine_id subtitle), Duration, Segments, Created, Actions.
- **Actions:** Delete button (red, only for non-active sessions).
- **Filters:** Status (All/Active/Completed/Error/Interrupted), Sort, Page size.
- **Delete dialog:** Confirmation modal.
- **Mobile:** Stacked card layout.

## Behaviour

- Real-time status fetched via `useRealtimeStatus()` — separate from session list.
- Sessions fetched via `useRealtimeSessions()` with infinite pagination.
- Row click → navigates to `/realtime/sessions/:sessionId`.
- Active sessions cannot be deleted (no delete button shown).
