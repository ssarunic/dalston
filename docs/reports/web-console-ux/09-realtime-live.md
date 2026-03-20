# 09 — Real-time Live Session

**Route:** `/realtime/live`
**Component:** `src/pages/RealtimeLive.tsx`
**Auth required:** Yes

## Purpose

Browser-based real-time transcription. Captures microphone audio, streams it to the backend via WebSocket, and displays live transcript with interim results.

## Storyboard

### Idle State

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│  [←] Live Transcription         [⚙ Settings ▾]              │
│      Speak into your microphone                              │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ Language: [Auto-detect ▾]    Model: [Any available ▾]  │  │
│  │ Vocabulary: [Kubernetes, PostgreSQL, ...]               │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│                ┌──────────────────────┐                      │
│                │    🎤 Start Session   │                      │
│                └──────────────────────┘                      │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ Transcript          00:00   0 words   0 segments       │  │
│  │                                                        │  │
│  │                                                        │  │
│  │           Tap "Start Session" and begin                 │  │
│  │           speaking to see the transcript               │  │
│  │           appear here.                                 │  │
│  │                                                        │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### Recording State

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│  [←] Live Transcription                                      │
│                                                              │
│                ┌──────────────────────┐                      │
│                │    ■ Stop Session     │  (red/destructive)   │
│                └──────────────────────┘                      │
│                                                              │
│                ● ████████████░░░░░░░░  (audio level meter)   │
│                🔴 Recording                                   │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ Transcript          02:15   187 words   12 segments    │  │
│  │                                                        │  │
│  │  Welcome everyone to today's meeting. I'd like to      │  │
│  │  start by reviewing the quarterly results that were    │  │
│  │  published last week.                                  │  │
│  │                                                        │  │
│  │  The numbers look really promising this quarter. We    │  │
│  │  saw a 15% increase in user engagement and the         │  │
│  │  retention rate improved significantly.                │  │
│  │                                                        │  │
│  │  ░ Let me share my screen and walk through the...      │  │
│  │    (partial/interim text, faded)                        │  │
│  │                                                        │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### Completed State

```
│                                                              │
│  ┌────────────────────────┐                                  │
│  │   🎤 Start Session     │   (ready for another)            │
│  └────────────────────────┘                                  │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ Transcript          05:02   412 words   28 segments    │  │
│  │ (final transcript preserved from session)               │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ Session completed   f7e2a1b4...    [View Details →]    │  │
│  └────────────────────────────────────────────────────────┘  │
```

## Elements

### Settings Panel (collapsible)
| Field | Type | Options |
|-------|------|---------|
| Language | Select | Auto-detect + 20 common languages |
| Model | Select | "Any available" + registered RT models (shows "(loaded)" suffix) |
| Vocabulary | Text input | Comma-separated custom terms |

Vocabulary hint dynamically shows if selected model supports vocabulary boosting.

### Action Buttons
| State | Button | Style |
|-------|--------|-------|
| Idle | "Start Session" | Primary, large, microphone icon |
| Connecting | "Connecting..." | Disabled, spinner |
| Recording | "Stop Session" | Destructive (red), square icon |
| Stopping | "Finishing..." | Disabled, spinner |

### Audio Level Meter (`<AudioLevelMeter>`)
- Horizontal bar showing real-time audio input level.
- Green accent when speech detected (VAD active).
- Small dot indicator: green when speaking, gray when silent.

### Recording Indicator
- Red pulsing dot with "Recording" text.

### Live Transcript (`<LiveTranscript>`)
- Full-height scrollable area.
- Finalized segments in normal text.
- Partial/interim text in faded style at bottom.
- Auto-scrolls to bottom as new segments arrive.

### Post-Session Card
- Shows session ID + "View Details" button linking to session detail page.

## Behaviour

1. **Settings** panel can be toggled before starting. Disabled during active session.
2. **Start:** Requests microphone permission → establishes WebSocket → begins streaming PCM audio.
3. **Interim results:** Partial transcript appears faded at bottom, replaced by final segments.
4. **VAD indicator:** Green dot pulses when speech is detected.
5. **Stop:** Closes WebSocket gracefully, server finalizes session.
6. **Capacity warning:** If system is at capacity or unavailable, shows amber banner with link to engines page.
7. **Error handling:** Connection errors shown as red banner; user can start a new session.
8. **Full viewport height:** Layout uses `h-[calc(100vh-4rem)]` to fill available space.

## States

| State | Settings | Button | Audio Meter | Transcript |
|-------|----------|--------|-------------|------------|
| idle | Editable | Start | Hidden | Empty placeholder |
| connecting | Disabled | Connecting... | Shown (no level) | Empty |
| recording | Disabled | Stop | Active + VAD | Live updating |
| stopping | Disabled | Finishing... | Fading | Final text |
| completed | Editable | Start | Hidden | Preserved + post-session card |
| error | Editable | Start | Hidden | Error banner shown |
