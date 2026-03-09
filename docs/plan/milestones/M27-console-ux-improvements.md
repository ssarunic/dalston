# M27: Console UX Improvements

| | |
|---|---|
| **Goal** | Improve console usability with better navigation, search, and responsive design |
| **Duration** | 8-10 days |
| **Dependencies** | M10 (Web Console), M24 (Realtime Session Persistence) |
| **Deliverable** | Slide-over panels, audio player with transcript sync, search/filters, mobile-responsive UI |
| **Status** | Not Started |

## User Story

> *"As an admin reviewing transcripts, I can quickly navigate between items without losing my place, search across all transcripts, and review audio synced with text on any device."*

---

## Overview

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                         DALSTON CONSOLE (IMPROVED)                          │
├─────────────────────────────────────────────────────────────────────────────┤
│  Dashboard  │  Batch Jobs  │  Realtime  │  Engines  │  API Keys  │ Webhooks │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │  [🔍 Search...]  [📅 Date range ▼]  [Status: All ▼]  [Clear]        │   │
│  └─────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌─────────────────────────────────────────┬───────────────────────────┐   │
│  │                                         │                           │   │
│  │  ┌─────────────────────────────────┐   │   SLIDE-OVER PANEL        │   │
│  │  │ interview.mp3  [Completed] 2h   │ → │   ─────────────────        │   │
│  │  │ "Hello, thank you for joining"  │   │   job_abc123               │   │
│  │  └─────────────────────────────────┘   │                           │   │
│  │  ┌─────────────────────────────────┐   │   [▶ 00:00 ━━━━━━ 12:34]  │   │
│  │  │ meeting.wav    [Running]  15m   │   │   1× ▼                     │   │
│  │  │ Processing...                   │   │                           │   │
│  │  └─────────────────────────────────┘   │   00:00  Hello, thank...  │   │
│  │  ┌─────────────────────────────────┐   │   00:15  Speaker 2: ...   │   │
│  │  │ podcast.m4a    [Completed] 45m  │   │   00:32  Yes, I think...  │   │
│  │  │ "Welcome to the show today"     │   │                           │   │
│  │  └─────────────────────────────────┘   │   [SRT] [VTT] [TXT] [JSON] │   │
│  │                                         │                           │   │
│  │  Page 1 of 5  [< Prev] [Next >]        │   [✕ Close]               │   │
│  └─────────────────────────────────────────┴───────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘

Mobile View (< 768px):
┌─────────────────────┐
│ ≡  DALSTON          │
├─────────────────────┤
│ [🔍 Search...]      │
│ [🎛️ Filters]        │
├─────────────────────┤
│ ┌─────────────────┐ │
│ │ [Completed] 2h  │ │
│ │ interview.mp3   │ │
│ │ "Hello, thank"  │ │
│ │              → │ │
│ └─────────────────┘ │
│ ┌─────────────────┐ │
│ │ [Running]  15m  │ │
│ │ meeting.wav     │ │
│ │ Processing...   │ │
│ └─────────────────┘ │
└─────────────────────┘
```

---

## Phases

This milestone is organized into 4 phases that can be developed somewhat independently:

| Phase | Focus | Days | Priority |
|-------|-------|------|----------|
| 27.1 | Clickable Rows + Better List Content | 1-2 | High |
| 27.2 | Slide-over Detail Panel | 2-3 | High |
| 27.3 | Search, Filters & URL State | 2-3 | High |
| 27.4 | Audio Player with Transcript Sync | 2-3 | Medium |
| 27.5 | Long Transcript Handling | 1-2 | Medium |
| 27.6 | Responsive Design | 2-3 | Medium |
| 27.7 | Realtime Transcript Export | 0.5 | Low |
| 27.8 | Unified Dashboard Activity | 1 | Low |

---

## Phase 27.1: Clickable Rows + Better List Content

**Goal:** Make table rows clickable and show more useful information at a glance.

### Changes

**Batch Jobs List (`BatchJobs.tsx`):** Replace the View button with full-row click using `cursor-pointer hover:bg-accent` on `<TableRow>`. Row click calls `openDetailPanel(job.id)`. Action buttons (Cancel/Delete) use `stopPropagation` to avoid triggering row click.

*Follow existing component patterns in `web/src/components/`.*

**Updated columns:**

| Column | Content |
|--------|---------|
| **Job** | Filename (or label) + truncated ID below |
| **Status** | Badge (unchanged) |
| **Preview** | First ~60 chars of transcript or "Processing..." |
| **Duration** | Audio length (e.g., "12m 34s") |
| **Created** | Relative time ("2h ago") |
| **Actions** | Cancel/Delete only (View button removed) |

**Backend Change:** Add `preview` field to job list response in `/api/console/jobs`:
- Return `job.text[:60] + "..."` if text exceeds 60 chars, else `job.text`

**Realtime Sessions List (`RealtimeSessions.tsx`):** Same pattern — clickable rows, remove external link icon in favor of row click.

### Deliverables

- [ ] Clickable table rows with hover state
- [ ] Updated column layout with preview text
- [ ] Backend returns `preview` field
- [ ] Cancel/Delete buttons use `stopPropagation`
- [ ] Right chevron icon on row trailing edge (visual affordance)

---

## Phase 27.2: Slide-over Detail Panel

**Goal:** Open job/session details in a slide-over drawer instead of navigating away.

### Component Structure

```text
web/src/components/
├── SlideOverPanel.tsx      # Generic slide-over wrapper
├── JobDetailPanel.tsx      # Job detail content for panel
└── SessionDetailPanel.tsx  # Session detail content for panel
```

### Components

**`SlideOverPanel`** — Generic slide-over wrapper built on shadcn `Sheet`.

```tsx
interface SlideOverPanelProps {
  open: boolean
  onClose: () => void
  title: string
  children: React.ReactNode
  width?: 'md' | 'lg' | 'xl'  // 448px, 576px, 672px
}
```

Renders a right-side `SheetContent` with configurable width and a header. *Follow existing component patterns in `web/src/components/`.*

**`JobDetailPanel`** — Reuses existing `JobDetail.tsx` content adapted for panel: removes back button (replaced by panel close), keeps metadata cards, DAG viewer, transcript, and export buttons. Adds "Open Full Page" link for deep work.

**`SessionDetailPanel`** — Same pattern for realtime sessions.

### State Management

Use URL search params for panel state so direct links work: `?detail=job_abc123`. The list page reads `searchParams.get('detail')` to determine which panel is open. Setting/clearing this param opens/closes the panel.

### Deliverables

- [ ] `SlideOverPanel` component using shadcn Sheet
- [ ] `JobDetailPanel` component (panel-adapted job detail)
- [ ] `SessionDetailPanel` component (panel-adapted session detail)
- [ ] URL state for panel (`?detail=job_abc123`)
- [ ] "Open in new tab" link within panel
- [ ] Full-page routes still work for direct links

---

## Phase 27.3: Search, Filters & URL State

**Goal:** Add search by ID/text and date filtering with URL-persisted state.

### Components

**`FilterBar`** — Renders search input, date range picker, status dropdown, and clear button in a flex row.

```tsx
interface FilterBarProps {
  onSearch: (query: string) => void
  onDateRange: (from: Date | null, to: Date | null) => void
  onStatusFilter: (status: string) => void
  values: { search: string; dateFrom: Date | null; dateTo: Date | null; status: string }
}
```

Search input has a `Search` icon prefix. Date picker supports presets (today, last 7 days, last 30 days, custom). Status select offers All/Pending/Running/Completed/Failed. Clear button appears when any filter is active. *Follow existing component patterns in `web/src/components/`.*

**`DateRangePicker`** — Date range selector with preset buttons and custom date inputs.

### Hooks

**`useFilterState()`** — Returns `{ filters, setFilter }`. Reads `q`, `status`, `from`, `to`, `page` from URL search params. Setting any filter other than `page` resets pagination to 0.

### Backend Changes

Update `/api/console/jobs` to support search:

```python
@router.get("/jobs")
async def list_jobs(
    q: str | None = None,
    status: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 20,
    offset: int = 0,
): ...
```

Query logic:
- If `q` provided: filter where job ID, text, or filename matches (case-insensitive `ILIKE`)
- If `status` provided: filter by exact status match
- If `since`/`until` provided: filter `created_at` within range

### Deliverables

- [ ] `FilterBar` component with search, date picker, status filter
- [ ] `DateRangePicker` component with presets
- [ ] `useFilterState` hook for URL-synced filter state
- [ ] Backend search endpoint with `q`, `since`, `until` params
- [ ] Debounced search input (300ms)
- [ ] Filter state persists on navigation and page refresh

---

## Phase 27.4: Audio Player with Transcript Sync

**Goal:** Sticky audio player that syncs with transcript, enabling review workflows.

### Components

**`AudioPlayer`** — Sticky bottom bar with play/pause, time display, seekable progress slider, playback speed selector (0.5x-2x), and download button.

```tsx
interface AudioPlayerProps {
  src: string
  currentTime?: number
  onTimeUpdate?: (time: number) => void
  onPlay?: () => void
  onPause?: () => void
}
```

Syncs to external `currentTime` prop (e.g., from transcript click-to-seek). Reports playback position via `onTimeUpdate`. *Follow existing component patterns in `web/src/components/`.*

**`TranscriptViewer` (updated)** — Add `currentTime` and `onSegmentClick` props. Highlights the active segment (the one whose `start <= currentTime < end`) with `bg-primary/10 border-l-2 border-primary`. Clicking a segment calls `onSegmentClick(segment.start)` to seek audio.

### Integration in JobDetailPanel

`JobDetailPanel` wires them together: `AudioPlayer.onTimeUpdate` feeds `TranscriptViewer.currentTime`, and `TranscriptViewer.onSegmentClick` feeds `AudioPlayer.currentTime`.

### Backend Change

Add `audio_url` to job detail response — a presigned S3 URL (1-hour expiry) for the original audio file:

```python
@router.get("/{job_id}")
async def get_job(job_id: str): ...
```

Returns `{**job.dict(), "audio_url": presigned_url}` when `job.audio_key` exists.

### Deliverables

- [ ] `AudioPlayer` component with play/pause, seek, speed control
- [ ] Transcript segment highlighting synced to playback position
- [ ] Click segment to seek audio
- [ ] Sticky player positioning in panel
- [ ] Backend returns signed `audio_url`
- [ ] Download button in player

---

## Phase 27.5: Long Transcript Handling

**Goal:** Efficiently render transcripts with 500+ segments using virtualization.

### Components

**`VirtualTranscriptViewer`** — Drop-in replacement for `TranscriptViewer` using `@tanstack/react-virtual`. Renders segments in a fixed-height container with estimated 48px row height and overscan of 10. Auto-scrolls to the active segment during playback using `scrollToIndex` with smooth behavior.

```tsx
interface VirtualTranscriptViewerProps {
  segments: Segment[]
  currentTime?: number
  onSegmentClick?: (time: number) => void
}
```

*Follow existing component patterns in `web/src/components/`.*

**`TranscriptMinimap`** — Thin vertical bar (3px wide) showing speaker distribution as colored segments proportional to timeline position. Clickable to seek to any point. Shows a current-position indicator line.

```tsx
interface TranscriptMinimapProps {
  segments: Segment[]
  totalDuration: number
  currentTime?: number
  onSeek: (time: number) => void
}
```

### Auto-switch Logic

In `JobDetailPanel`, use `VirtualTranscriptViewer` + `TranscriptMinimap` when segment count exceeds 100; use standard `TranscriptViewer` otherwise.

### Deliverables

- [ ] `VirtualTranscriptViewer` using `@tanstack/react-virtual`
- [ ] Auto-scroll to active segment during playback
- [ ] `TranscriptMinimap` with speaker visualization
- [ ] Auto-switch to virtual scroll above 100 segments
- [ ] Sticky timestamp header showing current time region
- [ ] Jump-to-time input for direct navigation

---

## Phase 27.6: Responsive Design

**Goal:** Usable console on tablet and mobile devices.

### Breakpoint Strategy

| Breakpoint | Width | Layout Changes |
|------------|-------|----------------|
| Desktop | ≥1024px | Full tables, slide-over panel, sidebar visible |
| Tablet | 768-1023px | Tables drop columns, full-width overlay, sidebar collapsible |
| Mobile | <768px | Card list, full-page detail, bottom sheet filters |

### Components

**`JobCard`** — Mobile-friendly card showing status badge, relative time, filename, preview text, duration, and a chevron. Replaces table rows on mobile.

```tsx
interface JobCardProps {
  job: JobSummary
  onClick: () => void
}
```

*Follow existing component patterns in `web/src/components/`.*

**`JobList`** — Responsive wrapper that renders `JobCard` list on mobile (`< 768px`) and the standard `Table` on desktop. Uses `useMediaQuery` hook.

**`MobileFilterSheet`** — Bottom sheet (80vh) containing search input, date range picker, and status toggle buttons with Apply/Clear footer. Opens from a filter icon button on mobile.

**`Layout` (updated)** — On mobile, sidebar becomes a left-side `Sheet` triggered by a hamburger menu. Desktop keeps the always-visible sidebar. Mobile gets a sticky header with hamburger + "Dalston" title.

### Column Visibility

Use Tailwind responsive classes on table headers/cells: `hidden lg:table-cell` for Duration, `hidden md:table-cell` for Language.

### Deliverables

- [ ] `useMediaQuery` hook for responsive logic
- [ ] `JobCard` component for mobile list view
- [ ] `MobileFilterSheet` component (bottom sheet)
- [ ] Responsive sidebar (collapsible on mobile)
- [ ] Table column hiding at breakpoints
- [ ] Detail view: slide-over on desktop, full-page on mobile
- [ ] Touch-friendly tap targets (min 44px)

---

## Phase 27.7: Realtime Transcript Export

**Goal:** Download realtime session transcripts in same formats as batch jobs.

### Backend Endpoint

```python
@router.get("/sessions/{session_id}/export/{format}")
async def export_session_transcript(
    session_id: str,
    format: Literal["txt", "json", "srt", "vtt"],
): ...
```

Fetches transcript from storage, converts to the requested format (plain text, JSON, SRT subtitles, or VTT subtitles), and returns as a file download with appropriate `Content-Disposition` header.

### Frontend

**`ExportButtons`** — Renders a row of download buttons (TXT, JSON, SRT, VTT) linking to the export endpoint. Reuse in `SessionDetailPanel`. Match styling of batch job export buttons.

### Deliverables

- [ ] Backend export endpoint for realtime sessions
- [ ] SRT/VTT generation from utterances
- [ ] Export buttons in session detail view
- [ ] Same button styling as batch job exports

---

## Phase 27.8: Unified Dashboard Activity

**Goal:** Show combined recent activity from both batch jobs and realtime sessions.

### Backend Endpoint

```python
@router.get("/activity")
async def get_recent_activity(limit: int = 10): ...
```

Fetches recent batch jobs and realtime sessions, merges into a unified list sorted by timestamp descending. Each activity item includes: `type` (batch/realtime), `id`, `status`, `label`, `timestamp`, `duration`.

### Components

**`RecentActivity`** — Renders a list of activity items. Each row shows a type icon (FileText for batch, Radio for realtime), status badge, label, and relative timestamp. Rows link to the appropriate detail page.

```tsx
interface Activity {
  type: 'batch' | 'realtime'
  id: string
  status: string
  label: string
  timestamp: string
  duration: number
}
```

*Follow existing component patterns in `web/src/components/`.*

**Dashboard integration** — Add filter tabs (All / Batch / Realtime) above the activity list. Replace the existing "Recent Jobs" section with `RecentActivity`.

### Deliverables

- [ ] Backend `/api/console/activity` endpoint
- [ ] `RecentActivity` component with type icons
- [ ] Filter tabs (All / Batch / Realtime)
- [ ] Replace "Recent Jobs" section on dashboard

---

## New Dependencies

```json
{
  "@tanstack/react-virtual": "^3.0.0",
  "date-fns": "^3.0.0"
}
```

---

## Verification

- [ ] Click a job row — slide-over opens with detail; Escape or outside click closes it; URL shows `?detail=job_xxx`
- [ ] Type in search — list filters by ID/transcript; date range and status filters work; URL reflects all filter state
- [ ] Open a completed job — Play button works, clicking a transcript segment seeks audio, active segment highlights during playback
- [ ] Resize to mobile (375px) — cards replace table, sidebar collapses to hamburger menu, filters open as bottom sheet
- [ ] Open a job with 500+ segments — scroll is smooth (virtualized), minimap shows speaker distribution

---

## Checkpoint

- [ ] **Clickable rows** open slide-over panel
- [ ] **Slide-over panel** shows job/session detail without leaving list
- [ ] **URL state** persists filters, pagination, and detail panel
- [ ] **Search** filters by ID and transcript text
- [ ] **Date range** and status filters work
- [ ] **Audio player** syncs with transcript (click segment to seek)
- [ ] **Long transcripts** render smoothly with virtualization
- [ ] **Mobile layout** uses cards and bottom sheet filters
- [ ] **Realtime export** supports TXT/JSON/SRT/VTT
- [ ] **Dashboard activity** shows mixed batch and realtime

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Performance with 1000+ segments | Virtualization with @tanstack/react-virtual |
| Audio CORS issues | Use signed S3 URLs with appropriate CORS config |
| Mobile touch targets too small | Minimum 44px tap targets, test on real devices |
| Search too slow | Debounce 300ms, limit results, consider adding DB index |
| Panel state lost on refresh | URL-based state management |

---

## Future Considerations

Not in scope for M27, but worth tracking:

- **Keyboard shortcuts**: j/k for list navigation, Enter to open, Escape to close
- **Bulk operations**: Select multiple jobs for delete/export
- **Transcript editing**: Correct transcription errors inline
- **Annotations**: Mark sections, add comments
- **Sharing**: Public links to specific transcript timestamps

**Next:** These could become M28 (Power User Features) once M27 is complete.
