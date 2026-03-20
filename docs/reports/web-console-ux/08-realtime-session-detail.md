# 08 — Real-time Session Detail

**Route:** `/realtime/sessions/:sessionId`
**Component:** `src/pages/RealtimeSessionDetail.tsx`
**Auth required:** Yes

## Purpose

Inspect a completed (or active) real-time transcription session: metadata, audio playback, and stored transcript.

## Storyboard

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│  [←]  f7e2a1b4-c3d9-4e5f-8a1b...              ● Completed  │
│       Real-time Session                                      │
│                                                              │
│  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌──────────┐  │
│  │Duration│ │Segments│ │ Words  │ │ Model  │ │Retention │  │
│  │        │ │        │ │        │ │        │ │          │  │
│  │ 5m 2s  │ │   28   │ │  412   │ │parakeet│ │ 30 days  │  │
│  └────────┘ └────────┘ └────────┘ └────────┘ │ 28d left │  │
│                                               └──────────┘  │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ Session Details                                        │  │
│  │                                                        │  │
│  │  Language      │ en         Encoding    │ pcm_s16le    │  │
│  │  Sample Rate   │ 16000 Hz   Instance    │ wkr-abc123   │  │
│  │  Client IP     │ 192.168..  Started At  │ Mar 20, 3:40 │  │
│  │  Ended At      │ Mar 20...                             │  │
│  │  Previous Sess │ a1b2c3... →                           │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ Transcript                                             │  │
│  │                                                        │  │
│  │  ▶ ━━━━━━━━━━━━━━━━━━━━━━ 0:00 / 5:02               │  │
│  │  [⬇ Download ▾]  [📋 Export ▾]                        │  │
│  │                                                        │  │
│  │  00:00  Welcome to the live demo session.              │  │
│  │  00:08  Today we're testing the realtime               │  │
│  │         transcription capabilities.                    │  │
│  │  00:15  The audio is being processed in real           │  │
│  │         time with word-level timestamps.               │  │
│  │  ...                                                   │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

## Sections

### 1. Header
- Back button (← to `/realtime`)
- Session ID in monospace (truncated)
- "Real-time Session" subtitle
- StatusBadge (active/completed/error/interrupted)

### 2. Error Banner (conditional)
- Shown when `session.error` exists.
- Red banner with AlertCircle icon and error message.

### 3. Stat Cards (5-column grid, responsive)
| Card | Icon | Value |
|------|------|-------|
| Duration | Clock | Formatted duration |
| Segments | MessageSquare | Segment count |
| Words | Hash | Word count |
| Model | Cpu | Model name or engine ID |
| Retention | Archive | Policy display + countdown or "Transient"/"Permanent"/"Purged" |

### 4. Session Details Card
Definition list (`<dl>`) in 2-column grid:
- Language, Encoding, Sample Rate, Instance (mono), Client IP, Started At, Ended At
- Previous Session: Link to prior session if this was a continuation

### 5. Transcript / Audio Card
Uses shared `<TranscriptViewer>` component:
- Audio player (presigned URL fetched lazily)
- Utterances rendered as timed segments
- Export options (SRT, VTT, TXT, JSON)
- Hidden when no transcript and no audio available (transient sessions)

## Behaviour

- Session data from `useRealtimeSession(sessionId)`.
- Transcript fetched separately from `useSessionTranscript()` — only if `retention ≠ 0` and `transcript_uri` exists.
- Audio URL fetched lazily for non-transient sessions with stored audio.
- Loading: returns null (blank).
- Error/Not found: back link + error banner.
