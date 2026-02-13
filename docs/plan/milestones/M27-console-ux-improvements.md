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

**Batch Jobs List (`BatchJobs.tsx`):**

```tsx
// Replace View button with full-row click
<TableRow
  key={job.id}
  className="cursor-pointer hover:bg-accent transition-colors"
  onClick={() => openDetailPanel(job.id)}
>
  <TableCell>
    <div className="flex flex-col">
      <span className="font-medium">{job.filename || job.id.slice(0, 8)}</span>
      <span className="text-xs text-muted-foreground font-mono">{job.id.slice(0, 8)}...</span>
    </div>
  </TableCell>
  <TableCell><StatusBadge status={job.status} /></TableCell>
  <TableCell className="text-muted-foreground text-sm max-w-[200px] truncate">
    {job.preview || (job.status === 'running' ? 'Processing...' : '-')}
  </TableCell>
  <TableCell>{formatDuration(job.audio_duration)}</TableCell>
  <TableCell>{formatRelativeTime(job.created_at)}</TableCell>
  <TableCell onClick={(e) => e.stopPropagation()}>
    {/* Cancel/Delete buttons only */}
  </TableCell>
</TableRow>
```

**New Columns:**

| Column | Content |
|--------|---------|
| **Job** | Filename (or label) + truncated ID below |
| **Status** | Badge (unchanged) |
| **Preview** | First ~60 chars of transcript or "Processing..." |
| **Duration** | Audio length (e.g., "12m 34s") |
| **Created** | Relative time ("2h ago") |
| **Actions** | Cancel/Delete only (View button removed) |

**Backend Change:**

Add `preview` field to job list response in `/api/console/jobs`:

```python
# In console.py
"preview": job.text[:60] + "..." if job.text and len(job.text) > 60 else job.text
```

**Realtime Sessions List (`RealtimeSessions.tsx`):**

Same pattern — clickable rows, remove external link icon in favor of row click.

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

### SlideOverPanel Component

```tsx
// SlideOverPanel.tsx
interface SlideOverPanelProps {
  open: boolean
  onClose: () => void
  title: string
  children: React.ReactNode
  width?: 'md' | 'lg' | 'xl'  // 448px, 576px, 672px
}

export function SlideOverPanel({ open, onClose, title, children, width = 'lg' }: SlideOverPanelProps) {
  return (
    <Sheet open={open} onOpenChange={onClose}>
      <SheetContent side="right" className={cn(widthClasses[width])}>
        <SheetHeader>
          <SheetTitle>{title}</SheetTitle>
        </SheetHeader>
        {children}
      </SheetContent>
    </Sheet>
  )
}
```

### State Management

Use URL for panel state so direct links work:

```tsx
// BatchJobs.tsx
const [searchParams, setSearchParams] = useSearchParams()
const selectedJobId = searchParams.get('detail')

const openDetailPanel = (jobId: string) => {
  setSearchParams(prev => {
    prev.set('detail', jobId)
    return prev
  })
}

const closeDetailPanel = () => {
  setSearchParams(prev => {
    prev.delete('detail')
    return prev
  })
}

return (
  <>
    {/* Table */}
    <SlideOverPanel
      open={!!selectedJobId}
      onClose={closeDetailPanel}
      title={`Job ${selectedJobId?.slice(0, 8)}...`}
    >
      {selectedJobId && <JobDetailPanel jobId={selectedJobId} />}
    </SlideOverPanel>
  </>
)
```

### Panel Content

Reuse existing `JobDetail.tsx` content but adapted for panel:

- Remove back button (replaced by panel close)
- Keep metadata cards, DAG viewer, transcript, export buttons
- Add "Open Full Page" link for deep work

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

### Filter Bar Component

```tsx
// FilterBar.tsx
interface FilterBarProps {
  onSearch: (query: string) => void
  onDateRange: (from: Date | null, to: Date | null) => void
  onStatusFilter: (status: string) => void
  values: {
    search: string
    dateFrom: Date | null
    dateTo: Date | null
    status: string
  }
}

export function FilterBar({ onSearch, onDateRange, onStatusFilter, values }: FilterBarProps) {
  return (
    <div className="flex flex-wrap gap-3 items-center">
      {/* Search input */}
      <div className="relative flex-1 min-w-[200px]">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <Input
          placeholder="Search ID or transcript..."
          className="pl-9"
          value={values.search}
          onChange={(e) => onSearch(e.target.value)}
        />
      </div>

      {/* Date range picker */}
      <DateRangePicker
        from={values.dateFrom}
        to={values.dateTo}
        onChange={onDateRange}
        presets={['today', 'last7days', 'last30days', 'custom']}
      />

      {/* Status filter */}
      <Select value={values.status} onValueChange={onStatusFilter}>
        <SelectTrigger className="w-[130px]">
          <SelectValue placeholder="All statuses" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="">All</SelectItem>
          <SelectItem value="pending">Pending</SelectItem>
          <SelectItem value="running">Running</SelectItem>
          <SelectItem value="completed">Completed</SelectItem>
          <SelectItem value="failed">Failed</SelectItem>
        </SelectContent>
      </Select>

      {/* Clear button */}
      {hasActiveFilters && (
        <Button variant="ghost" size="sm" onClick={clearFilters}>
          Clear filters
        </Button>
      )}
    </div>
  )
}
```

### URL State Hook

```tsx
// useFilterState.ts
export function useFilterState() {
  const [searchParams, setSearchParams] = useSearchParams()

  const filters = {
    search: searchParams.get('q') || '',
    status: searchParams.get('status') || '',
    dateFrom: searchParams.get('from') ? new Date(searchParams.get('from')!) : null,
    dateTo: searchParams.get('to') ? new Date(searchParams.get('to')!) : null,
    page: parseInt(searchParams.get('page') || '0'),
  }

  const setFilter = (key: string, value: string | null) => {
    setSearchParams(prev => {
      if (value) {
        prev.set(key, value)
      } else {
        prev.delete(key)
      }
      // Reset to page 0 when filters change
      if (key !== 'page') {
        prev.delete('page')
      }
      return prev
    })
  }

  return { filters, setFilter }
}
```

### Backend Changes

Update `/api/console/jobs` to support search:

```python
@router.get("/jobs")
async def list_jobs(
    q: str | None = None,           # Search query
    status: str | None = None,
    since: datetime | None = None,  # Date range start
    until: datetime | None = None,  # Date range end
    limit: int = 20,
    offset: int = 0,
):
    query = select(Job)

    if q:
        # Search in job ID and transcript text
        query = query.where(
            or_(
                Job.id.ilike(f"%{q}%"),
                Job.text.ilike(f"%{q}%"),
                Job.filename.ilike(f"%{q}%"),
            )
        )

    if status:
        query = query.where(Job.status == status)

    if since:
        query = query.where(Job.created_at >= since)

    if until:
        query = query.where(Job.created_at <= until)

    # ... pagination
```

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

### AudioPlayer Component

```tsx
// AudioPlayer.tsx
interface AudioPlayerProps {
  src: string
  currentTime?: number
  onTimeUpdate?: (time: number) => void
  onPlay?: () => void
  onPause?: () => void
}

export function AudioPlayer({ src, currentTime, onTimeUpdate, onPlay, onPause }: AudioPlayerProps) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const [isPlaying, setIsPlaying] = useState(false)
  const [duration, setDuration] = useState(0)
  const [progress, setProgress] = useState(0)
  const [playbackRate, setPlaybackRate] = useState(1)

  // Sync external currentTime (e.g., from transcript click)
  useEffect(() => {
    if (currentTime !== undefined && audioRef.current) {
      audioRef.current.currentTime = currentTime
    }
  }, [currentTime])

  return (
    <div className="sticky bottom-0 bg-background border-t p-3 flex items-center gap-4">
      {/* Play/Pause */}
      <Button variant="ghost" size="icon" onClick={togglePlay}>
        {isPlaying ? <Pause /> : <Play />}
      </Button>

      {/* Time display */}
      <span className="text-sm font-mono w-24">
        {formatTime(progress)} / {formatTime(duration)}
      </span>

      {/* Progress bar (clickable to seek) */}
      <Slider
        value={[progress]}
        max={duration}
        step={0.1}
        onValueChange={([v]) => seek(v)}
        className="flex-1"
      />

      {/* Playback speed */}
      <Select value={String(playbackRate)} onValueChange={(v) => setPlaybackRate(Number(v))}>
        <SelectTrigger className="w-16">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="0.5">0.5×</SelectItem>
          <SelectItem value="0.75">0.75×</SelectItem>
          <SelectItem value="1">1×</SelectItem>
          <SelectItem value="1.25">1.25×</SelectItem>
          <SelectItem value="1.5">1.5×</SelectItem>
          <SelectItem value="2">2×</SelectItem>
        </SelectContent>
      </Select>

      {/* Download button */}
      <Button variant="ghost" size="icon" asChild>
        <a href={src} download><Download className="h-4 w-4" /></a>
      </Button>

      <audio ref={audioRef} src={src} />
    </div>
  )
}
```

### Transcript Sync

```tsx
// TranscriptViewer.tsx (updated)
interface TranscriptViewerProps {
  segments: Segment[]
  speakers?: Speaker[]
  currentTime?: number           // From audio player
  onSegmentClick?: (time: number) => void  // Seek audio
}

export function TranscriptViewer({ segments, currentTime, onSegmentClick }: TranscriptViewerProps) {
  const activeSegmentIndex = useMemo(() => {
    if (currentTime === undefined) return -1
    return segments.findIndex(s => currentTime >= s.start && currentTime < s.end)
  }, [segments, currentTime])

  return (
    <div className="space-y-1">
      {segments.map((segment, idx) => (
        <div
          key={segment.id}
          className={cn(
            "flex gap-4 py-2 px-2 rounded cursor-pointer hover:bg-accent/50 transition-colors",
            idx === activeSegmentIndex && "bg-primary/10 border-l-2 border-primary"
          )}
          onClick={() => onSegmentClick?.(segment.start)}
        >
          <span className="text-xs text-muted-foreground font-mono w-12">
            {formatTime(segment.start)}
          </span>
          {segment.speaker && (
            <span className="text-xs font-medium w-20" style={{ color: speakerColor }}>
              {segment.speaker}
            </span>
          )}
          <span className="flex-1 text-sm">{segment.text}</span>
        </div>
      ))}
    </div>
  )
}
```

### Integration in JobDetailPanel

```tsx
// JobDetailPanel.tsx
export function JobDetailPanel({ jobId }: { jobId: string }) {
  const { data: job } = useJob(jobId)
  const [audioTime, setAudioTime] = useState<number>()
  const [seekTo, setSeekTo] = useState<number>()

  const audioUrl = job?.audio_url // Add to job detail response

  return (
    <div className="flex flex-col h-full">
      {/* Scrollable content */}
      <div className="flex-1 overflow-y-auto">
        {/* Metadata cards */}
        {/* ... */}

        {/* Transcript with click-to-seek */}
        <TranscriptViewer
          segments={job?.segments ?? []}
          currentTime={audioTime}
          onSegmentClick={setSeekTo}
        />
      </div>

      {/* Sticky player at bottom */}
      {audioUrl && (
        <AudioPlayer
          src={audioUrl}
          currentTime={seekTo}
          onTimeUpdate={setAudioTime}
        />
      )}
    </div>
  )
}
```

### Backend Change

Add `audio_url` to job detail response (signed URL for S3):

```python
# In v1/transcriptions.py
@router.get("/{job_id}")
async def get_job(job_id: str):
    job = await job_service.get(job_id)

    audio_url = None
    if job.audio_key:
        audio_url = await storage.get_presigned_url(job.audio_key, expires_in=3600)

    return {
        **job.dict(),
        "audio_url": audio_url,
    }
```

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

### Virtual Scroll Implementation

```tsx
// VirtualTranscriptViewer.tsx
import { useVirtualizer } from '@tanstack/react-virtual'

interface VirtualTranscriptViewerProps {
  segments: Segment[]
  currentTime?: number
  onSegmentClick?: (time: number) => void
}

export function VirtualTranscriptViewer({ segments, currentTime, onSegmentClick }: VirtualTranscriptViewerProps) {
  const parentRef = useRef<HTMLDivElement>(null)

  const virtualizer = useVirtualizer({
    count: segments.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 48, // Estimated row height
    overscan: 10,
  })

  // Auto-scroll to active segment during playback
  const activeIndex = useMemo(() => {
    if (currentTime === undefined) return -1
    return segments.findIndex(s => currentTime >= s.start && currentTime < s.end)
  }, [segments, currentTime])

  useEffect(() => {
    if (activeIndex >= 0) {
      virtualizer.scrollToIndex(activeIndex, { align: 'center', behavior: 'smooth' })
    }
  }, [activeIndex])

  return (
    <div ref={parentRef} className="h-[500px] overflow-auto">
      <div
        style={{
          height: `${virtualizer.getTotalSize()}px`,
          width: '100%',
          position: 'relative',
        }}
      >
        {virtualizer.getVirtualItems().map((virtualRow) => {
          const segment = segments[virtualRow.index]
          return (
            <div
              key={virtualRow.key}
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                width: '100%',
                height: `${virtualRow.size}px`,
                transform: `translateY(${virtualRow.start}px)`,
              }}
            >
              <TranscriptSegment
                segment={segment}
                isActive={virtualRow.index === activeIndex}
                onClick={() => onSegmentClick?.(segment.start)}
              />
            </div>
          )
        })}
      </div>
    </div>
  )
}
```

### Timeline Minimap

```tsx
// TranscriptMinimap.tsx
interface TranscriptMinimapProps {
  segments: Segment[]
  totalDuration: number
  currentTime?: number
  onSeek: (time: number) => void
}

export function TranscriptMinimap({ segments, totalDuration, currentTime, onSeek }: TranscriptMinimapProps) {
  // Group segments by speaker and render colored bars
  const speakerTracks = useMemo(() => {
    const tracks: Record<string, { start: number; end: number }[]> = {}
    segments.forEach(s => {
      const speaker = s.speaker || 'unknown'
      if (!tracks[speaker]) tracks[speaker] = []
      tracks[speaker].push({ start: s.start, end: s.end })
    })
    return tracks
  }, [segments])

  return (
    <div
      className="w-3 bg-muted rounded cursor-pointer relative"
      onClick={(e) => {
        const rect = e.currentTarget.getBoundingClientRect()
        const ratio = (e.clientY - rect.top) / rect.height
        onSeek(ratio * totalDuration)
      }}
    >
      {/* Speaker color bars */}
      {Object.entries(speakerTracks).map(([speaker, ranges], idx) => (
        ranges.map((range, i) => (
          <div
            key={`${speaker}-${i}`}
            className="absolute left-0 right-0"
            style={{
              top: `${(range.start / totalDuration) * 100}%`,
              height: `${((range.end - range.start) / totalDuration) * 100}%`,
              backgroundColor: speakerColors[idx % speakerColors.length],
              opacity: 0.7,
            }}
          />
        ))
      ))}

      {/* Current position indicator */}
      {currentTime !== undefined && (
        <div
          className="absolute left-0 right-0 h-0.5 bg-primary"
          style={{ top: `${(currentTime / totalDuration) * 100}%` }}
        />
      )}
    </div>
  )
}
```

### Auto-switch Based on Segment Count

```tsx
// In JobDetailPanel or TranscriptCard
const VIRTUALIZATION_THRESHOLD = 100

{segments.length > VIRTUALIZATION_THRESHOLD ? (
  <div className="flex gap-2">
    <VirtualTranscriptViewer
      segments={segments}
      currentTime={audioTime}
      onSegmentClick={setSeekTo}
    />
    <TranscriptMinimap
      segments={segments}
      totalDuration={job.audio_duration}
      currentTime={audioTime}
      onSeek={setSeekTo}
    />
  </div>
) : (
  <TranscriptViewer
    segments={segments}
    currentTime={audioTime}
    onSegmentClick={setSeekTo}
  />
)}
```

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

### Mobile Card Component

```tsx
// JobCard.tsx
interface JobCardProps {
  job: JobSummary
  onClick: () => void
}

export function JobCard({ job, onClick }: JobCardProps) {
  return (
    <div
      className="p-4 border rounded-lg cursor-pointer hover:bg-accent/50 transition-colors"
      onClick={onClick}
    >
      <div className="flex items-start justify-between mb-2">
        <StatusBadge status={job.status} />
        <span className="text-xs text-muted-foreground">
          {formatRelativeTime(job.created_at)}
        </span>
      </div>
      <div className="font-medium truncate">{job.filename || job.id.slice(0, 12)}</div>
      <div className="text-sm text-muted-foreground truncate mt-1">
        {job.preview || '-'}
      </div>
      <div className="flex items-center justify-between mt-3">
        <span className="text-xs text-muted-foreground">
          {formatDuration(job.audio_duration)}
        </span>
        <ChevronRight className="h-4 w-4 text-muted-foreground" />
      </div>
    </div>
  )
}
```

### Responsive List Component

```tsx
// JobList.tsx
export function JobList({ jobs, onSelect }: JobListProps) {
  const isMobile = useMediaQuery('(max-width: 767px)')

  if (isMobile) {
    return (
      <div className="space-y-3">
        {jobs.map(job => (
          <JobCard key={job.id} job={job} onClick={() => onSelect(job.id)} />
        ))}
      </div>
    )
  }

  return (
    <Table>
      {/* ... existing table implementation */}
    </Table>
  )
}
```

### Mobile Filter Sheet

```tsx
// MobileFilterSheet.tsx
export function MobileFilterSheet({ open, onOpenChange, filters, onApply }: Props) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="bottom" className="h-[80vh]">
        <SheetHeader>
          <SheetTitle>Filters</SheetTitle>
        </SheetHeader>
        <div className="space-y-6 py-4">
          {/* Search */}
          <div>
            <Label>Search</Label>
            <Input placeholder="ID or transcript text..." />
          </div>

          {/* Date range */}
          <div>
            <Label>Date range</Label>
            <DateRangePicker />
          </div>

          {/* Status */}
          <div>
            <Label>Status</Label>
            <div className="flex flex-wrap gap-2 mt-2">
              {statuses.map(s => (
                <Button
                  key={s.value}
                  variant={selected === s.value ? 'default' : 'outline'}
                  size="sm"
                >
                  {s.label}
                </Button>
              ))}
            </div>
          </div>
        </div>
        <SheetFooter>
          <Button variant="outline" onClick={onClear}>Clear</Button>
          <Button onClick={onApply}>Apply Filters</Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  )
}
```

### Responsive Sidebar

```tsx
// Layout.tsx
export function Layout({ children }: { children: React.ReactNode }) {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const isMobile = useMediaQuery('(max-width: 767px)')

  return (
    <div className="flex min-h-screen">
      {/* Desktop sidebar */}
      {!isMobile && <Sidebar />}

      {/* Mobile sidebar (sheet) */}
      {isMobile && (
        <Sheet open={sidebarOpen} onOpenChange={setSidebarOpen}>
          <SheetContent side="left" className="w-64 p-0">
            <Sidebar onNavigate={() => setSidebarOpen(false)} />
          </SheetContent>
        </Sheet>
      )}

      <main className="flex-1">
        {/* Mobile header with hamburger */}
        {isMobile && (
          <header className="sticky top-0 z-40 border-b bg-background px-4 py-3 flex items-center">
            <Button variant="ghost" size="icon" onClick={() => setSidebarOpen(true)}>
              <Menu className="h-5 w-5" />
            </Button>
            <span className="ml-3 font-semibold">Dalston</span>
          </header>
        )}

        <div className="p-4 md:p-6">{children}</div>
      </main>
    </div>
  )
}
```

### Column Visibility by Breakpoint

```tsx
// In BatchJobs.tsx table
<TableHead className="hidden lg:table-cell">Duration</TableHead>
<TableHead className="hidden md:table-cell">Language</TableHead>
```

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
# In v1/realtime.py
@router.get("/sessions/{session_id}/export/{format}")
async def export_session_transcript(
    session_id: str,
    format: Literal["txt", "json", "srt", "vtt"],
):
    session = await session_service.get(session_id)
    if not session or not session.transcript_uri:
        raise HTTPException(404, "Transcript not found")

    transcript = await storage.get_json(session.transcript_uri)

    if format == "txt":
        content = transcript.get("text", "")
        media_type = "text/plain"
    elif format == "json":
        content = json.dumps(transcript, indent=2)
        media_type = "application/json"
    elif format == "srt":
        content = utterances_to_srt(transcript.get("utterances", []))
        media_type = "text/plain"
    elif format == "vtt":
        content = utterances_to_vtt(transcript.get("utterances", []))
        media_type = "text/vtt"

    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename=session_{session_id[:8]}.{format}"}
    )
```

### Frontend Component

```tsx
// In RealtimeSessionDetail.tsx
function ExportButtons({ sessionId }: { sessionId: string }) {
  const formats = ['txt', 'json', 'srt', 'vtt'] as const

  return (
    <div className="flex gap-2">
      {formats.map((format) => (
        <a
          key={format}
          href={`/v1/realtime/sessions/${sessionId}/export/${format}?api_key=${getApiKey()}`}
          download
        >
          <Button variant="outline" size="sm">
            <Download className="h-3 w-3 mr-1" />
            {format.toUpperCase()}
          </Button>
        </a>
      ))}
    </div>
  )
}
```

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
# In console.py
@router.get("/activity")
async def get_recent_activity(limit: int = 10):
    """Combined recent batch jobs and realtime sessions."""

    # Fetch recent from both sources
    jobs = await job_service.list(limit=limit, order_by="-created_at")
    sessions = await session_service.list(limit=limit, order_by="-started_at")

    # Merge and sort
    activities = []

    for job in jobs:
        activities.append({
            "type": "batch",
            "id": job.id,
            "status": job.status,
            "label": job.filename or job.id[:8],
            "timestamp": job.created_at,
            "duration": job.audio_duration,
        })

    for session in sessions:
        activities.append({
            "type": "realtime",
            "id": session.id,
            "status": session.status,
            "label": f"Session {session.id[:8]}",
            "timestamp": session.started_at,
            "duration": session.audio_duration_seconds,
        })

    # Sort by timestamp descending
    activities.sort(key=lambda x: x["timestamp"], reverse=True)

    return {"activities": activities[:limit]}
```

### Dashboard Component

```tsx
// RecentActivity.tsx
interface Activity {
  type: 'batch' | 'realtime'
  id: string
  status: string
  label: string
  timestamp: string
  duration: number
}

export function RecentActivity({ activities }: { activities: Activity[] }) {
  return (
    <div className="divide-y">
      {activities.map(activity => (
        <Link
          key={`${activity.type}-${activity.id}`}
          to={activity.type === 'batch' ? `/jobs/${activity.id}` : `/realtime/sessions/${activity.id}`}
          className="flex items-center justify-between py-3 px-2 hover:bg-accent transition-colors"
        >
          <div className="flex items-center gap-3">
            {activity.type === 'batch' ? (
              <FileText className="h-4 w-4 text-muted-foreground" />
            ) : (
              <Radio className="h-4 w-4 text-muted-foreground" />
            )}
            <StatusBadge status={activity.status} />
            <span className="text-sm">{activity.label}</span>
          </div>
          <span className="text-sm text-muted-foreground">
            {formatRelativeTime(activity.timestamp)}
          </span>
        </Link>
      ))}
    </div>
  )
}
```

### Filter Tabs

```tsx
// In Dashboard.tsx
const [activityFilter, setActivityFilter] = useState<'all' | 'batch' | 'realtime'>('all')

const filteredActivities = activities.filter(a =>
  activityFilter === 'all' || a.type === activityFilter
)

<div className="flex gap-2 mb-4">
  <Button
    variant={activityFilter === 'all' ? 'default' : 'outline'}
    size="sm"
    onClick={() => setActivityFilter('all')}
  >
    All
  </Button>
  <Button
    variant={activityFilter === 'batch' ? 'default' : 'outline'}
    size="sm"
    onClick={() => setActivityFilter('batch')}
  >
    Batch
  </Button>
  <Button
    variant={activityFilter === 'realtime' ? 'default' : 'outline'}
    size="sm"
    onClick={() => setActivityFilter('realtime')}
  >
    Realtime
  </Button>
</div>
```

### Deliverables

- [ ] Backend `/api/console/activity` endpoint
- [ ] `RecentActivity` component with type icons
- [ ] Filter tabs (All / Batch / Realtime)
- [ ] Replace "Recent Jobs" section on dashboard

---

## New Dependencies

```json
// package.json additions
{
  "dependencies": {
    "@tanstack/react-virtual": "^3.0.0",
    "date-fns": "^3.0.0"
  }
}
```

---

## File Changes Summary

### New Files

```text
web/src/components/
├── SlideOverPanel.tsx
├── JobDetailPanel.tsx
├── SessionDetailPanel.tsx
├── FilterBar.tsx
├── DateRangePicker.tsx
├── AudioPlayer.tsx
├── VirtualTranscriptViewer.tsx
├── TranscriptMinimap.tsx
├── JobCard.tsx
├── SessionCard.tsx
├── MobileFilterSheet.tsx
└── RecentActivity.tsx

web/src/hooks/
├── useFilterState.ts
└── useMediaQuery.ts
```

### Modified Files

```text
web/src/pages/BatchJobs.tsx       # Clickable rows, filters, slide-over integration
web/src/pages/RealtimeSessions.tsx # Clickable rows, slide-over integration
web/src/pages/Dashboard.tsx        # Unified activity feed
web/src/pages/JobDetail.tsx        # Audio player integration
web/src/components/Layout.tsx      # Responsive sidebar
web/src/components/TranscriptViewer.tsx # Click-to-seek, active highlighting
web/src/api/client.ts              # New endpoints

dalston/gateway/api/console.py     # Search params, activity endpoint
dalston/gateway/api/v1/realtime.py # Export endpoint
dalston/gateway/api/v1/transcriptions.py # audio_url in response
```

---

## Verification

```bash
# Desktop testing
open http://localhost:8000/console

# Verify clickable rows
# Click a job row → slide-over opens with detail
# Press Escape or click outside → panel closes
# URL shows ?detail=job_xxx

# Verify search/filters
# Type in search → list filters by ID/transcript
# Select date range → list filters
# Change status → list filters
# URL shows ?q=hello&status=completed&from=2024-01-01

# Verify audio player
# Open a completed job
# Click Play → audio plays
# Click transcript segment → audio seeks
# Segment highlights during playback

# Verify responsive
# Resize to 768px → columns hide, panel becomes full-width
# Resize to 375px → cards replace table, sidebar collapses

# Mobile testing with device emulation
open http://localhost:8000/console (Chrome DevTools → iPhone 14)

# Verify filters on mobile
# Tap filter icon → bottom sheet opens
# Apply filters → sheet closes, list filters

# Verify long transcript
# Open a job with 500+ segments
# Scroll is smooth (virtualized)
# Minimap shows speaker distribution
```

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
