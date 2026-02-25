# M34: Audio Player with Transcript Sync

|                  |                                                                                         |
| ---------------- | --------------------------------------------------------------------------------------- |
| **Goal**         | Integrated audio player that syncs with transcript, enabling efficient review workflows |
| **Duration**     | 2-3 days                                                                                |
| **Dependencies** | M10 (Web Console), M24 (Realtime Session Persistence)                                   |
| **Deliverable**  | AudioPlayer component, click-to-seek, segment highlighting, playback controls           |
| **Status**       | Completed                                                                               |

## User Story

> *"As a reviewer listening to a transcript, I can click any segment to jump to that moment in the audio, see which segment is currently playing, and adjust playback speed to review tricky sections."*

---

## Overview

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│  Transcript                                                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│  ▶ ═══●═══ 2:34/12:45 🔊 1× ⟳ ⬇ │ 🛡 [Original│Redacted] │ Export ▾        │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  0:00   Speaker A   Hello, how are you today?                               │
│  0:05   Speaker B   I'm doing well, thanks for asking.                      │
│ [0:12]  Speaker A   Great! Let's get started with the interview.  ◀─ Active │
│  0:18   Speaker B   Sounds good to me.                                      │
│  0:25   Speaker A   First question: tell me about your background.          │
│  ...                                                                         │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘

Legend:
  ▶       Play/Pause toggle (Plyr native controls)
  ═●═     Seek slider (current position)
  1×      Playback speed (Plyr settings menu: 0.5×, 0.75×, 1×, 1.25×, 1.5×, 2×)
  ⟳       Auto-scroll toggle (follow playback)
  ⬇       Download button (downloads current audio variant)
  🛡       PII toggle (Original/Redacted text AND audio)
  Export ▾ Export dropdown (SRT, VTT, TXT, JSON formats)
  [0:12]  Active segment (highlighted, auto-scrolled to)
```

---

## Design Decisions

| Question | Decision | Rationale |
|----------|----------|-----------|
| Player library? | Plyr (~30KB) | Lightweight HTML5 player with native controls. Replaced wavesurfer.js (~150KB) for smaller bundle and simpler UX. Future stereo visualization can be added separately. |
| Player position? | Header bar (not sticky) | In same row as PII toggle and export buttons. Compact layout. |
| Both batch & realtime? | Yes | TranscriptViewer already serves both. Pass optional `audioSrc` prop. |
| Playback speed? | Yes | Essential for reviewing transcripts: 0.5×, 0.75×, 1×, 1.25×, 1.5×, 2× |
| Click segment to seek? | Yes | Core value prop - transforms the UX |
| Auto-scroll during playback? | Optional, default off | Some users find it disorienting. Toggle in player. |
| Highlight current segment? | Yes | Subtle background tint on active segment |
| Keyboard shortcuts? | Yes | Space (play/pause), ← → (seek ±5s) |

---

## Component Architecture

```text
TranscriptViewer (modified)
├── Header bar
│   ├── AudioPlayer (Plyr-based)
│   │   ├── Plyr controls (play, progress, time, mute, speed)
│   │   ├── Auto-scroll toggle
│   │   └── Download button (downloads current audio variant)
│   ├── PII toggle (Original/Redacted)
│   └── Export dropdown (SRT/VTT/TXT/JSON)
└── TranscriptSegmentRow (modified)
    ├── onClick → seek to segment.start
    └── isActive prop → highlight style
```

---

## Implementation Steps

### 34.1: AudioPlayer Component

**Deliverable:** Standalone, reusable audio player component.

**File:** `web/src/components/AudioPlayer.tsx`

```tsx
interface AudioPlayerProps {
  src: string
  onTimeUpdate?: (time: number) => void
  seekTo?: number  // External seek request (from segment click)
}

export function AudioPlayer({ src, onTimeUpdate, seekTo }: AudioPlayerProps) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const [isPlaying, setIsPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [playbackRate, setPlaybackRate] = useState(1)
  const [autoScroll, setAutoScroll] = useState(false)

  // Handle external seek requests
  useEffect(() => {
    if (seekTo !== undefined && audioRef.current) {
      audioRef.current.currentTime = seekTo
      if (!isPlaying) {
        audioRef.current.play()
        setIsPlaying(true)
      }
    }
  }, [seekTo])

  // Sync playback rate
  useEffect(() => {
    if (audioRef.current) {
      audioRef.current.playbackRate = playbackRate
    }
  }, [playbackRate])

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement) return

      switch (e.code) {
        case 'Space':
          e.preventDefault()
          togglePlay()
          break
        case 'ArrowLeft':
          seek(currentTime - 5)
          break
        case 'ArrowRight':
          seek(currentTime + 5)
          break
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [currentTime])

  const togglePlay = () => {
    if (!audioRef.current) return
    if (isPlaying) {
      audioRef.current.pause()
    } else {
      audioRef.current.play()
    }
    setIsPlaying(!isPlaying)
  }

  const seek = (time: number) => {
    if (!audioRef.current) return
    audioRef.current.currentTime = Math.max(0, Math.min(time, duration))
  }

  return (
    <div className="sticky top-0 z-10 bg-background border-b p-3 flex items-center gap-3">
      {/* Play/Pause */}
      <Button variant="ghost" size="icon" onClick={togglePlay}>
        {isPlaying ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />}
      </Button>

      {/* Time display */}
      <span className="text-sm font-mono text-muted-foreground w-24 text-center">
        {formatTime(currentTime)} / {formatTime(duration)}
      </span>

      {/* Seek slider */}
      <Slider
        value={[currentTime]}
        max={duration || 100}
        step={0.1}
        onValueChange={([v]) => seek(v)}
        className="flex-1"
      />

      {/* Playback speed */}
      <Select value={String(playbackRate)} onValueChange={(v) => setPlaybackRate(Number(v))}>
        <SelectTrigger className="w-16 h-8">
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

      {/* Auto-scroll toggle */}
      <Button
        variant={autoScroll ? 'secondary' : 'ghost'}
        size="icon"
        onClick={() => setAutoScroll(!autoScroll)}
        title={autoScroll ? 'Auto-scroll on' : 'Auto-scroll off'}
      >
        <RotateCcw className="h-4 w-4" />
      </Button>

      {/* Download */}
      <Button variant="ghost" size="icon" asChild>
        <a href={src} download title="Download audio">
          <Download className="h-4 w-4" />
        </a>
      </Button>

      {/* Hidden audio element */}
      <audio
        ref={audioRef}
        src={src}
        onTimeUpdate={() => {
          const time = audioRef.current?.currentTime ?? 0
          setCurrentTime(time)
          onTimeUpdate?.(time)
        }}
        onLoadedMetadata={() => setDuration(audioRef.current?.duration ?? 0)}
        onEnded={() => setIsPlaying(false)}
      />
    </div>
  )
}

function formatTime(seconds: number): string {
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins}:${secs.toString().padStart(2, '0')}`
}
```

**Deliverables:**

- [ ] Play/pause with button and Space key
- [ ] Time display (MM:SS / MM:SS)
- [ ] Seek slider (click/drag)
- [ ] Arrow keys seek ±5 seconds
- [ ] Playback speed dropdown (0.5× to 2×)
- [ ] Auto-scroll toggle button
- [ ] Download audio button
- [ ] Sticky positioning at top of transcript

---

### 34.2: TranscriptViewer Integration

**Deliverable:** Update TranscriptViewer to support audio sync.

**File:** `web/src/components/TranscriptViewer.tsx`

**New props:**

```tsx
export interface TranscriptViewerProps {
  segments: UnifiedSegment[]
  speakers?: Speaker[]
  fullText?: string
  enableExport?: boolean
  exportConfig?: ExportConfig
  piiConfig?: PIIConfig
  maxHeight?: string
  emptyMessage?: string
  // New audio props
  audioSrc?: string                        // If provided, show player
  onSegmentClick?: (segment: UnifiedSegment) => void  // External handler
}
```

**State management:**

```tsx
export function TranscriptViewer({
  segments,
  audioSrc,
  // ... other props
}: TranscriptViewerProps) {
  const [currentTime, setCurrentTime] = useState(0)
  const [seekTo, setSeekTo] = useState<number>()
  const segmentRefs = useRef<Map<string, HTMLDivElement>>(new Map())

  // Find active segment based on playback time
  const activeSegmentId = useMemo(() => {
    const active = segments.find(
      (s) => currentTime >= s.start && currentTime < s.end
    )
    return active?.id ?? null
  }, [currentTime, segments])

  // Auto-scroll to active segment
  useEffect(() => {
    if (activeSegmentId) {
      segmentRefs.current.get(activeSegmentId)?.scrollIntoView({
        behavior: 'smooth',
        block: 'center',
      })
    }
  }, [activeSegmentId])

  const handleSegmentClick = (segment: UnifiedSegment) => {
    setSeekTo(segment.start)
    onSegmentClick?.(segment)
  }

  return (
    <div className="space-y-0">
      {/* Audio player (sticky) */}
      {audioSrc && (
        <AudioPlayer
          src={audioSrc}
          onTimeUpdate={setCurrentTime}
          seekTo={seekTo}
        />
      )}

      {/* Header with PII toggle and export buttons */}
      {/* ... existing header ... */}

      {/* Transcript content */}
      <div className="overflow-y-auto" style={{ maxHeight }}>
        {hasSegments ? (
          <div>
            {segments.map((segment) => (
              <TranscriptSegmentRow
                key={segment.id}
                ref={(el) => {
                  if (el) segmentRefs.current.set(segment.id, el)
                }}
                segment={segment}
                speakerColors={speakerColors}
                showSpeakerColumn={!!hasSpeakers}
                showRedacted={piiConfig?.showRedacted}
                isActive={segment.id === activeSegmentId}
                onClick={() => handleSegmentClick(segment)}
              />
            ))}
          </div>
        ) : /* ... existing fallbacks ... */}
      </div>
    </div>
  )
}
```

**Update TranscriptSegmentRow:**

```tsx
interface TranscriptSegmentRowProps {
  segment: UnifiedSegment
  speakerColors: Record<string, string>
  showSpeakerColumn: boolean
  showRedacted?: boolean
  isActive?: boolean      // New
  onClick?: () => void    // New
}

const TranscriptSegmentRow = forwardRef<HTMLDivElement, TranscriptSegmentRowProps>(
  ({ segment, speakerColors, showSpeakerColumn, showRedacted, isActive, onClick }, ref) => {
    const speakerColor = segment.speaker ? speakerColors[segment.speaker] : undefined
    const displayText = showRedacted && segment.redacted_text ? segment.redacted_text : segment.text

    return (
      <div
        ref={ref}
        className={cn(
          "flex gap-4 py-3 px-2 border-b border-border last:border-0 transition-colors",
          onClick && "cursor-pointer hover:bg-muted/50",
          isActive && "bg-primary/10 border-l-2 border-l-primary"
        )}
        onClick={onClick}
      >
        <div className="w-16 flex-shrink-0 text-xs text-muted-foreground font-mono">
          {formatTime(segment.start)}
        </div>
        {showSpeakerColumn && segment.speaker && (
          <div
            className="w-24 flex-shrink-0 text-xs font-medium"
            style={{ color: speakerColor }}
          >
            {segment.speaker}
          </div>
        )}
        <div className="flex-1 text-sm">{displayText}</div>
      </div>
    )
  }
)
```

**Deliverables:**

- [ ] `audioSrc` prop enables player
- [ ] Segment click seeks audio to `segment.start`
- [ ] Active segment highlighted with left border + background
- [ ] Auto-scroll to active segment (when enabled)
- [ ] Segment refs for scroll targeting
- [ ] forwardRef on TranscriptSegmentRow

---

### 34.3: JobDetail Integration

**Deliverable:** Pass audio URL to TranscriptViewer in batch job detail.

**File:** `web/src/pages/JobDetail.tsx`

**Changes:**

```tsx
export function JobDetail() {
  const { jobId } = useParams()
  const { data: job, isLoading, error } = useJob(jobId)
  const [audioUrl, setAudioUrl] = useState<string | null>(null)
  const [audioLoading, setAudioLoading] = useState(false)

  // Fetch audio URL for completed jobs
  useEffect(() => {
    if (job?.status === 'completed' && !job.retention?.purged_at) {
      setAudioLoading(true)
      apiClient.getJobAudioUrl(job.id)
        .then(({ url }) => setAudioUrl(url))
        .catch((err) => console.error('Failed to get audio URL:', err))
        .finally(() => setAudioLoading(false))
    }
  }, [job?.id, job?.status, job?.retention?.purged_at])

  // ... existing code ...

  return (
    <div className="space-y-6">
      {/* ... existing header, error, metadata ... */}

      {/* Transcript with audio player */}
      {job.status === 'completed' && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base font-medium">Transcript</CardTitle>
          </CardHeader>
          <CardContent className="p-0">  {/* Remove padding for sticky player */}
            <TranscriptViewer
              segments={job.segments ?? []}
              speakers={job.speakers}
              fullText={job.text}
              audioSrc={audioUrl ?? undefined}  // Pass audio URL
              enableExport={true}
              exportConfig={{ type: 'job', id: job.id }}
              piiConfig={job.pii?.enabled ? {
                enabled: true,
                entitiesDetected: job.pii.entities_detected,
                redactedText: job.redacted_text,
                onToggle: setShowRedacted,
                showRedacted,
              } : undefined}
            />
          </CardContent>
        </Card>
      )}

      {/* ... rest of page ... */}
    </div>
  )
}
```

**Deliverables:**

- [ ] Fetch audio URL on job load (completed jobs only)
- [ ] Pass `audioSrc` to TranscriptViewer
- [ ] Handle purged audio (no player shown)
- [ ] Remove CardContent padding for sticky player

---

### 34.4: RealtimeSessionDetail Integration

**Deliverable:** Pass audio URL to TranscriptViewer in realtime session detail.

**File:** `web/src/pages/RealtimeSessionDetail.tsx`

**Changes:**

```tsx
export function RealtimeSessionDetail() {
  const { sessionId } = useParams<{ sessionId: string }>()
  const { data: session, isLoading, error } = useRealtimeSession(sessionId)
  const { data: transcript } = useSessionTranscript(
    sessionId,
    !!session?.store_transcript && !!session?.transcript_uri
  )
  const [audioUrl, setAudioUrl] = useState<string | null>(null)

  // Fetch audio URL for sessions with stored audio
  useEffect(() => {
    if (session?.store_audio && session?.audio_uri && sessionId) {
      apiClient.getSessionAudioUrl(sessionId)
        .then(({ url }) => setAudioUrl(url))
        .catch((err) => console.error('Failed to get audio URL:', err))
    }
  }, [session?.store_audio, session?.audio_uri, sessionId])

  // ... existing code ...

  return (
    <div className="space-y-6">
      {/* ... existing header, stats, details ... */}

      {/* Transcript Card with audio player */}
      {transcript && (
        <Card>
          <CardHeader>
            <CardTitle>Transcript</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <TranscriptViewer
              segments={transcript.utterances?.map(utt => ({
                id: utt.id,
                start: utt.start,
                end: utt.end,
                text: utt.text,
              })) ?? []}
              fullText={transcript.text}
              audioSrc={audioUrl ?? undefined}  // Pass audio URL
              enableExport={!!session.transcript_uri}
              exportConfig={{ type: 'session', id: session.id }}
            />
          </CardContent>
        </Card>
      )}

      {/* ... rest of page ... */}
    </div>
  )
}
```

**Deliverables:**

- [ ] Fetch audio URL when `store_audio` is true
- [ ] Pass `audioSrc` to TranscriptViewer
- [ ] Handle sessions without audio storage

---

## Edge Cases

| Case | Handling |
|------|----------|
| Audio purged | Don't show player, existing "Purged" state in Audio card |
| Audio still processing | Don't show player, status indicator in Audio card |
| No segments (text-only) | Player still works, no segment click/highlight |
| Very long audio (2+ hours) | Same UI, native audio element handles it |
| Mobile viewport | Player controls wrap, seek slider full width |
| Audio load error | Log error, don't show player |
| Segments without end times | Use next segment's start or audio duration |

---

## Verification

```bash
# 1. Start services
docker compose up -d gateway orchestrator redis postgres minio \
  stt-batch-prepare stt-batch-transcribe-whisper-cpu stt-batch-merge

# 2. Submit a job and wait for completion
JOB_ID=$(curl -s -X POST http://localhost:8000/v1/audio/transcriptions \
  -H "X-API-Key: $API_KEY" \
  -F "file=@test_audio.mp3" | jq -r '.id')

# Wait for completion
while [ "$(curl -s http://localhost:8000/v1/audio/transcriptions/$JOB_ID | jq -r '.status')" != "completed" ]; do
  sleep 5
done

# 3. Open console and verify
open http://localhost:5173/jobs/$JOB_ID

# Manual checks:
# - [ ] Audio player appears at top of transcript card
# - [ ] Play/pause button works
# - [ ] Seek slider updates during playback
# - [ ] Time display shows current/total
# - [ ] Click segment → audio seeks to that time
# - [ ] Active segment highlighted during playback
# - [ ] Playback speed dropdown works (0.5× to 2×)
# - [ ] Auto-scroll toggle works
# - [ ] Download button downloads audio
# - [ ] Space key toggles play/pause
# - [ ] Arrow keys seek ±5 seconds

# 4. Test realtime session (if audio storage enabled)
# Start a realtime session with store_audio=true
# Navigate to session detail, verify same behavior

# 5. Test edge cases
# - Job with purged audio → no player shown
# - Job still processing → no player shown
# - Empty transcript → player works, no segments to click
```

---

## Files Changed

| File | Change |
|------|--------|
| `web/src/components/AudioPlayer.tsx` | New - standalone audio player component |
| `web/src/components/TranscriptViewer.tsx` | Add audio integration, segment click, active highlight |
| `web/src/pages/JobDetail.tsx` | Fetch audio URL, pass to TranscriptViewer |
| `web/src/pages/RealtimeSessionDetail.tsx` | Fetch audio URL, pass to TranscriptViewer |

---

## Dependencies

New npm dependencies added:

- `plyr` (~30KB) - Lightweight HTML5 media player with native controls
- `@tanstack/react-virtual` - Virtualized scrolling for large transcript lists

Also uses:

- Existing shadcn/ui components (Button, DropdownMenu)
- Existing Lucide icons

---

## Checkpoint

- [x] `AudioPlayer` component with play/pause, seek, speed, download
- [x] Keyboard shortcuts (Space, ← →)
- [x] TranscriptViewer accepts `audioSrc` prop
- [x] Segment click seeks audio
- [x] Active segment highlighted
- [x] Auto-scroll to active segment (optional)
- [x] JobDetail fetches and passes audio URL
- [x] RealtimeSessionDetail fetches and passes audio URL
- [x] Purged/processing audio handled gracefully

---

## Enhancements Implemented (Beyond Original M34 Scope)

Core features implemented:

- [x] **Plyr player** - Lightweight HTML5 player with built-in speed control, progress bar
- [x] **Keyboard navigation** - j/k to jump between segments, Space to play/pause, arrows to seek
- [x] **Playback persistence** - remember position on page refresh (sessionStorage)
- [x] **Virtualized transcript** - @tanstack/react-virtual for 100+ segment transcripts
- [x] **Original/Redacted audio sync** - Player source changes with PII toggle
- [x] **Unified header bar** - Player, PII toggle, and exports in single row (not sticky)

Additional improvements:

- Stale audio URL prevention after navigation (derive URL validity from job/session ID)
- Error handling with retry for failed audio loads
- Optimized segment lookup (O(1) for continuous playback, O(log n) for seeks)
- Seek request IDs to handle repeated clicks on same segment
- Audio download button (downloads whichever variant is currently active)

Future enhancements (not yet implemented):

- [ ] Waveform/stereo visualization (can be added separately)
