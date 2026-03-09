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

The transcript view includes a header bar with an embedded Plyr-based audio player, PII toggle (Original/Redacted), and export dropdown. The transcript body shows timestamped, speaker-labeled segments. The currently-playing segment is highlighted and optionally auto-scrolled into view. Clicking any segment seeks the audio to that timestamp.

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

TranscriptViewer contains a header bar with the AudioPlayer (Plyr-based, with auto-scroll toggle and download button), PII toggle, and export dropdown. Each TranscriptSegmentRow supports `onClick` to seek to `segment.start` and an `isActive` prop for highlight styling.

---

## Implementation Steps

### 34.1: AudioPlayer Component

**File:** `web/src/components/AudioPlayer.tsx`

Standalone reusable audio player component with play/pause, seek slider, time display, playback speed selector (0.5x-2x), auto-scroll toggle, and download button. Uses a hidden `<audio>` element with Plyr controls. Exposes `onTimeUpdate` callback and accepts external `seekTo` prop. Keyboard shortcuts: Space for play/pause, arrow keys for ±5s seek. Input elements are excluded from keyboard capture.

**Deliverables:**

- [ ] Play/pause with button and Space key
- [ ] Time display (MM:SS / MM:SS)
- [ ] Seek slider (click/drag)
- [ ] Arrow keys seek ±5 seconds
- [ ] Playback speed dropdown (0.5× to 2×)
- [ ] Auto-scroll toggle button
- [ ] Download audio button

---

### 34.2: TranscriptViewer Integration

**File:** `web/src/components/TranscriptViewer.tsx`

Added `audioSrc` prop to conditionally render AudioPlayer, and `onSegmentClick` for external handlers. Tracks `currentTime` from the player to determine the active segment via `useMemo`. Active segment is highlighted with a left border and background tint. Auto-scroll uses `scrollIntoView` with smooth behavior. TranscriptSegmentRow was updated with `forwardRef`, `isActive`, and `onClick` props.

**Deliverables:**

- [ ] `audioSrc` prop enables player
- [ ] Segment click seeks audio to `segment.start`
- [ ] Active segment highlighted with left border + background
- [ ] Auto-scroll to active segment (when enabled)
- [ ] Segment refs for scroll targeting
- [ ] forwardRef on TranscriptSegmentRow

---

### 34.3: JobDetail Integration

**File:** `web/src/pages/JobDetail.tsx`

Fetches audio URL via `apiClient.getJobAudioUrl()` for completed jobs (skipping purged jobs) and passes `audioSrc` to TranscriptViewer. CardContent padding removed to support sticky player layout.

**Deliverables:**

- [ ] Fetch audio URL on job load (completed jobs only)
- [ ] Pass `audioSrc` to TranscriptViewer
- [ ] Handle purged audio (no player shown)

---

### 34.4: RealtimeSessionDetail Integration

**File:** `web/src/pages/RealtimeSessionDetail.tsx`

Fetches audio URL via `apiClient.getSessionAudioUrl()` for sessions with `store_audio` enabled and passes `audioSrc` to TranscriptViewer.

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

- [ ] Audio player appears at top of transcript card for completed jobs
- [ ] Play/pause, seek slider, time display, and speed dropdown all functional
- [ ] Click segment seeks audio; active segment highlighted during playback
- [ ] Keyboard shortcuts work (Space, arrow keys)
- [ ] Auto-scroll toggle and download button work
- [ ] Realtime session detail shows player when `store_audio` is true
- [ ] No player shown for purged or still-processing audio

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

## Future Enhancements

- [ ] Waveform/stereo visualization (can be added separately)
