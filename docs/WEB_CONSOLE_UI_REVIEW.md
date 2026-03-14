# Dalston Web Console — UI/UX Review Document

**Date:** 2026-03-14
**Purpose:** Provide a UX/UI designer with a complete picture of the current web console — its function, screens, layout, and behavior — to evaluate the interface and identify improvement opportunities.

---

## 1. Product Overview

Dalston is a self-hosted audio transcription server. The **web console** is its admin interface — a single-page React application for managing transcription jobs, monitoring real-time sessions, controlling engines and models, and administering API keys and webhooks.

### Primary User Personas

| Persona | Goal |
|---|---|
| **Operator** | Submit files, monitor pipeline status, download results |
| **Power User** | Fine-tune model/speaker/PII/retention options, review long transcripts |
| **Support Engineer** | Diagnose failures, inspect task artifacts, review audit logs |

### Technology Stack

- React 19 + TypeScript, Vite 7
- Tailwind CSS v4 with CSS variable theming (dark mode by default)
- shadcn/ui component patterns (Radix UI primitives)
- TanStack React Query v5 for server state
- React Router 7 for navigation
- Lucide icons, ky HTTP client, Plyr audio player
- `@tanstack/react-virtual` for list virtualization

---

## 2. Global Layout

```
Desktop (≥768px):
┌──────────────────────────────────────────────────────────────────┐
│ SIDEBAR (w-64, fixed)    │  MAIN CONTENT (flex-1, scrollable)   │
│                          │                                      │
│ ┌──────────────────────┐ │  ┌──────────────────────────────────┐│
│ │ DALSTON              │ │  │                                  ││
│ │ Transcription Console│ │  │  Page content here               ││
│ └──────────────────────┘ │  │                                  ││
│                          │  │                                  ││
│  Dashboard               │  │                                  ││
│  Batch Jobs              │  │                                  ││
│  Real-time               │  │                                  ││
│  Engines                 │  │                                  ││
│  Models                  │  │                                  ││
│  ───────────             │  │                                  ││
│  API Keys                │  │                                  ││
│  Webhooks                │  │                                  ││
│  Audit Log               │  │                                  ││
│  Settings                │  │                                  ││
│                          │  │                                  ││
│ ┌──────────────────────┐ │  │                                  ││
│ │ API Key: sk-...****  │ │  │                                  ││
│ │ [Logout]             │ │  └──────────────────────────────────┘│
│ └──────────────────────┘ │                                      │
└──────────────────────────────────────────────────────────────────┘

Mobile (<768px):
┌─────────────────────────┐
│ [≡]  DALSTON            │  ← Sticky header with hamburger
├─────────────────────────┤
│                         │
│  Page content           │  ← Full-width, single column
│                         │
└─────────────────────────┘

Hamburger opens sidebar in a Sheet (slide-in drawer from left).
```

### Floating Indicators

Two floating indicators can appear over any screen:

1. **LiveSessionIndicator** — Appears when a real-time recording session is active. Clicking navigates to the live session page.
2. **DownloadIndicator** — Appears when model downloads are in progress. Clicking shows a popover with download progress for each model.

### Navigation Items (9 total)

| Nav Item | Icon | Route |
|---|---|---|
| Dashboard | LayoutDashboard | `/` |
| Batch Jobs | ListTodo | `/jobs` |
| Real-time | Radio | `/realtime` |
| Engines | Server | `/engines` |
| Models | Package | `/models` |
| API Keys | Key | `/keys` |
| Webhooks | Webhook | `/webhooks` |
| Audit Log | ScrollText | `/audit` |
| Settings | Settings | `/settings` |

Active nav item has an accent background highlight.

---

## 3. Screen-by-Screen Specification

---

### 3.1 Login

**Route:** `/login`
**Purpose:** Authenticate with an API key before accessing the console.

```
┌───────────────────────────────────────┐
│                                       │
│           DALSTON                     │
│     Transcription Console             │
│                                       │
│  ┌─────────────────────────────────┐  │
│  │  API Key: [________________]    │  │
│  └─────────────────────────────────┘  │
│                                       │
│           [Sign In]                   │
│                                       │
└───────────────────────────────────────┘
```

**Behavior:**
- Single text input for API key
- Key stored in `sessionStorage` (cleared on tab close)
- On success: redirects to Dashboard
- On failure: shows error message inline
- No username/password — API key is the sole auth mechanism

---

### 3.2 Dashboard

**Route:** `/`
**Purpose:** System overview — health at a glance, recent activity, capabilities.

```
┌──────────────────────────────────────────────────────────────────┐
│  Dashboard                                                       │
│                                                                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐           │
│  │ System   │ │ Running  │ │ Active   │ │ Completed│           │
│  │ Status   │ │ Jobs     │ │ Sessions │ │ Today    │           │
│  │ Healthy  │ │ 3        │ │ 1        │ │ 47       │           │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘           │
│                                                                  │
│  ┌─────────────────────────────┐ ┌──────────────────────────┐   │
│  │  Recent Jobs                │ │  System Capabilities     │   │
│  │  ─────────────────────────  │ │  ────────────────────    │   │
│  │  interview.mp3  Completed   │ │  ✓ Word Timestamps      │   │
│  │  meeting.wav    Running     │ │  ✓ Speaker Diarization   │   │
│  │  podcast.m4a    Completed   │ │  ✓ PII Detection         │   │
│  │  call_01.mp3    Failed      │ │  ✓ Real-time Streaming   │   │
│  │  lecture.wav    Completed   │ │                          │   │
│  └─────────────────────────────┘ │  Models Ready: 5/12     │   │
│                                  │  [View All Models →]    │   │
│  ┌─────────────────────────────┐ └──────────────────────────┘   │
│  │  Recent Sessions            │                                │
│  │  ─────────────────────────  │ ┌──────────────────────────┐   │
│  │  Session abc12  Completed   │ │  Throughput (24h)        │   │
│  │  Session def34  Completed   │ │  ┌─ ─ ─ ─ ─ ─ ─ ─┐     │   │
│  │  Session ghi56  Active      │ │  │ █ █ ▄ █ █ ▄ ▂ █│     │   │
│  └─────────────────────────────┘ │  └─ ─ ─ ─ ─ ─ ─ ─┘     │   │
│                                  │  (stacked bar: ok/fail)  │   │
│                                  └──────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

**Layout:** Responsive grid — 4 stat cards across top (2×2 on mobile), then 2-column grid for activity/capabilities sections (stacks on mobile).

**Behavior:**
- Stat cards show live counts; auto-refresh via React Query (staleTime: 30s)
- Recent Jobs/Sessions are links — clicking navigates to the detail page
- Capabilities card reflects what models are ready (e.g., timestamps supported only if a timestamps-capable model is ready)
- Metrics panel shows hourly throughput as a stacked bar chart (completed vs. failed)
- Real-time health status indicator shows system health

---

### 3.3 Batch Jobs

**Route:** `/jobs`
**Purpose:** List all transcription jobs, filter by status, create new jobs.

```
Desktop:
┌──────────────────────────────────────────────────────────────────┐
│  Batch Jobs                              [+ Submit Job]          │
│                                                                  │
│  [Status: All ▼]  [Sort: Newest ▼]                              │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ Job           │ Status    │ Model     │ Duration │ Created │  │
│  │───────────────│──────────│──────────│─────────│────────│  │
│  │ interview.mp3 │ ● Done   │ large-v3 │ 12m 34s │ 2h ago │  │
│  │ meeting.wav   │ ◉ Run... │ auto     │ 45m 12s │ 3h ago │  │
│  │ podcast.m4a   │ ● Done   │ large-v3 │ 1h 02m  │ 5h ago │  │
│  │ call_01.mp3   │ ● Failed │ medium   │ 3m 22s  │ 1d ago │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  Showing 4 of 47                           [Load More]           │
└──────────────────────────────────────────────────────────────────┘

Mobile:
┌─────────────────────────┐
│  Batch Jobs    [+ New]  │
│  [Status ▼] [Sort ▼]   │
│                         │
│  ┌─────────────────────┐│
│  │ ● Completed   2h ago││
│  │ interview.mp3       ││
│  │ 12m 34s          →  ││
│  └─────────────────────┘│
│  ┌─────────────────────┐│
│  │ ◉ Running     3h ago││
│  │ meeting.wav         ││
│  │ 45m 12s          →  ││
│  └─────────────────────┘│
└─────────────────────────┘
```

**Behavior:**
- **Status filter:** Dropdown with options: All, Pending, Running, Completed, Failed, Cancelled
- **Sort:** Newest first / Oldest first
- **Pagination:** "Load More" button (infinite query, cursor-based)
- **Row actions:**
  - Cancel (available on pending/running jobs) — shows confirmation dialog
  - Delete (available on completed/failed jobs) — shows confirmation dialog
- **Mobile:** Switches from table to card layout below 768px
- **Empty state:** Shows "No jobs yet" with CTA to submit first job
- **"Submit Job" button** navigates to `/jobs/new`
- Clicking a row navigates to job detail page
- Toast notifications on successful cancel/delete

---

### 3.4 Submit Job (New Job)

**Route:** `/jobs/new`
**Purpose:** Create a new transcription job via file upload or audio URL.

```
Desktop (two-column):
┌──────────────────────────────────────────────────────────────────┐
│  ← Back to Jobs                                                  │
│  Submit Batch Job                                                │
│  Upload audio or provide an audio URL to create a transcription  │
│  job.                                                            │
│                                                                  │
│  ┌──────────────────────────────────┐ ┌────────────────────────┐│
│  │  Source                          │ │  Summary               ││
│  │  [Upload File] [Audio URL]       │ │                        ││
│  │                                  │ │  Source: interview.mp3 ││
│  │  ┌────────────────────────────┐  │ │  Language: Auto        ││
│  │  │  Drop file here or click   │  │ │  Speakers: Diarize     ││
│  │  │  to browse                 │  │ │  Timestamps: Word      ││
│  │  │  MP3, WAV, FLAC, OGG, M4A │  │ │  Model: Auto           ││
│  │  └────────────────────────────┘  │ │                        ││
│  └──────────────────────────────────┘ │                        ││
│                                       │                        ││
│  ┌──────────────────────────────────┐ │  ┌──────────────────┐  ││
│  │  Basic Settings                  │ │  │  Guidance         │  ││
│  │                                  │ │  │                   │  ││
│  │  Language: [Auto ▼]              │ │  │  For most files,  │  ││
│  │  Speaker detection: [Diarize ▼]  │ │  │  defaults work    │  ││
│  │  Timestamps: [Word-level ▼]      │ │  │  well. Use        │  ││
│  └──────────────────────────────────┘ │  │  Advanced for     │  ││
│                                       │  │  specific needs.  │  ││
│  ┌──────────────────────────────────┐ │  └──────────────────┘  ││
│  │  ▸ Advanced Settings (collapsed) │ └────────────────────────┘│
│  │    Model, Vocabulary, Retention, │                           │
│  │    PII options                   │                           │
│  └──────────────────────────────────┘                           │
│                                                                  │
│  [Cancel]                              [Submit Job]              │
└──────────────────────────────────────────────────────────────────┘
```

**Layout:**
- Desktop: 2/3 form + 1/3 summary sidebar
- Mobile: Single column, sticky bottom action bar with Cancel/Submit

**Source Card:**
- Segmented control toggles between "Upload File" and "Audio URL"
- File mode: dropzone + file input; shows selected filename
- URL mode: text input for HTTPS URL

**Basic Settings:**
- Language: dropdown with 31 languages + "Auto (detect)"
- Speaker detection: None / Diarize / Per-channel
  - When Diarize selected: optional num/min/max speaker fields appear
- Timestamps: None / Segment-level / Word-level

**Advanced Settings (collapsed accordion):**
- Model override: searchable dropdown (Auto recommended, grouped by engine)
- Custom vocabulary: tag/chips input (max 100 terms)
- Retention policy: dropdown from server-provided policies
- PII detection toggle → reveals: PII tier (fast/standard/thorough), entity types, redact audio toggle → reveals: redaction mode (silence/beep)

**Behavior:**
- Progressive disclosure: advanced settings collapsed by default
- Client-side validation before submit (missing source, cross-field speaker validation)
- Server errors mapped to user-friendly messages shown inline under relevant fields
- On submit: all inputs disabled, button shows "Submitting..."
- On success: redirect to `/jobs/:id`, show "Job submitted" toast
- On failure: form stays visible with error, user can fix and resubmit

**Conditional field visibility:**
| Condition | Fields shown |
|---|---|
| Source = File | File dropzone |
| Source = URL | URL text input |
| Speaker = Diarize/Per-channel | Num/Min/Max speakers |
| PII detection = On | PII tier, entity types, redact audio |
| Redact audio = On | Redaction mode |

---

### 3.5 Job Detail

**Route:** `/jobs/:id`
**Purpose:** Monitor a job's progress, view pipeline DAG, read transcript, play audio.

```
┌──────────────────────────────────────────────────────────────────┐
│  ← Back to Jobs                                                  │
│  Job: abc123                                    ● Completed      │
│                                                                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐           │
│  │ Duration │ │ Language │ │ Words    │ │ Segments │           │
│  │ 12m 34s  │ │ English  │ │ 2,847    │ │ 156      │           │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘           │
│  ┌──────────┐ ┌──────────┐                                      │
│  │ Speakers │ │ Retained │                                      │
│  │ 3        │ │ 28 days  │                                      │
│  └──────────┘ └──────────┘                                      │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  Pipeline                                                  │  │
│  │                                                            │  │
│  │  PREPARE → TRANSCRIBE → ALIGN → DIARIZE → PII → MERGE    │  │
│  │    ●          ●           ●       ●        ○       ●      │  │
│  │  (done)     (done)      (done)  (done)  (skip)  (done)    │  │
│  │                                                            │  │
│  │  Click any task node to view its artifacts and details     │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  Transcript                         [SRT] [VTT] [TXT] [JSON]│
│  │  ──────────                                                │  │
│  │  00:00  Speaker 1  Hello, thank you for joining us today.  │  │
│  │  00:05  Speaker 2  Thanks for having me.                   │  │
│  │  00:08  Speaker 1  Let's start with the first topic...     │  │
│  │  00:15  Speaker 2  Sure. I think the key issue is...       │  │
│  │  ...                                                       │  │
│  │                     [▶ Audio Player]                        │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  Audit Trail                                               │  │
│  │  ──────────                                                │  │
│  │  2h ago  Job created                                       │  │
│  │  2h ago  Processing started                                │  │
│  │  1h ago  Transcription completed                           │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

**Metadata Grid:** 4-6 stat cards showing audio duration, detected language, word count, segment count, speaker count, and retention period.

**Error Card:** When status is "failed", a red card appears showing the error message with parsed diagnostic info.

**Pipeline / DAG Viewer:**
- Visual representation of the task pipeline stages
- Each stage shows as a node with status: pending (gray), ready (yellow), running (blue, pulse animation), completed (green), failed (red), skipped (gray light), cancelled (orange)
- Arrow connectors between dependent stages
- Stages: PREPARE → TRANSCRIBE → ALIGN → DIARIZE → PII_DETECT → AUDIO_REDACT → MERGE
- Multi-stage engines may collapse stages (e.g., whisperx-full does transcribe+align+diarize)
- **Clickable** — clicking a task node navigates to Task Detail page
- Shows duration and model name per task

**Transcript Viewer:**
- Time-indexed segments with speaker labels
- Color-coded speakers (6-color rotation: blue, green, yellow, purple, pink, cyan)
- PII redaction toggle — when enabled, PII entities shown as highlighted placeholders
- Export dropdown: SRT, VTT, TXT, JSON formats
- Virtual scrolling kicks in above 100 segments
- Full-text view mode available
- Click a segment to seek audio player to that timestamp

**Audio Player (Plyr-based):**
- Play/pause, seek bar, playback speed (0.5× to 2×)
- Toggle between original and redacted audio (if PII audio redaction was performed)
- Current playback position synced with transcript viewer — active segment highlighted

**Retention Info:** Shows how long the job data is retained and when it will be purged.

**Audit Trail:** Chronological list of events (created, started, stage transitions, completed/failed) with timestamps and action badges.

---

### 3.6 Task Detail

**Route:** `/jobs/:jobId/tasks/:taskId`
**Purpose:** Inspect a single pipeline task's inputs, outputs, and artifacts.

```
┌──────────────────────────────────────────────────────────────────┐
│  ← Back to Job                                                   │
│  Task: transcribe (whisper-large-v3)              ● Completed    │
│                                                                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐                        │
│  │ Duration │ │ Started  │ │ Retries  │                        │
│  │ 4m 12s   │ │ 14:02:33 │ │ 0        │                        │
│  └──────────┘ └──────────┘ └──────────┘                        │
│                                                                  │
│  Dependencies: [prepare ✓]                                       │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  ▾ Input Artifacts                                         │  │
│  │  ──────────────                                            │  │
│  │  { "audio_path": "/data/abc123/prepared.wav",              │  │
│  │    "language": "en", ... }                                 │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  ▾ Output Artifacts                                        │  │
│  │  ──────────────                                            │  │
│  │  [Stage-specific viewer]                                   │  │
│  │  - Transcribe: segment list with timestamps                │  │
│  │  - Diarize: speaker timeline                               │  │
│  │  - Align: word-level timing                                │  │
│  │  - Merge: final combined output                            │  │
│  │  - Prepare: audio metadata                                 │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

**Behavior:**
- Metadata cards: task duration, start/complete timestamps, retry count
- Dependency visualization: shows upstream tasks as linked badges
- Input/output sections are collapsible
- **Stage-specific output viewers** render differently per pipeline stage:
  - Transcribe: segment list
  - Diarize: speaker assignments
  - Align: word-level timestamps
  - Merge: combined final output
  - Prepare: audio file metadata
- Raw JSON viewer available for all artifacts
- Error details section (if task failed) with retry count

---

### 3.7 Real-time Sessions

**Route:** `/realtime`
**Purpose:** View real-time session history, system capacity, and start new sessions.

```
┌──────────────────────────────────────────────────────────────────┐
│  Real-time Sessions                        [Start Live Session]  │
│                                                                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐           │
│  │ System   │ │ Active   │ │ Healthy  │ │ Capacity │           │
│  │ Status   │ │ Sessions │ │ Workers  │ │ 1 / 4    │           │
│  │ ● Ready  │ │ 1        │ │ 3        │ │ ████░░░░ │           │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘           │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  ℹ  System is ready for real-time sessions                 │  │
│  │     [▸ Show details]                                       │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  [Status: All ▼]  [Sort: Newest ▼]                              │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ Session     │ Status    │ Duration  │ Segments │ Started  │  │
│  │─────────────│──────────│──────────│─────────│─────────│  │
│  │ sess_abc12  │ ● Done   │ 5m 23s   │ 34      │ 1h ago  │  │
│  │ sess_def34  │ ● Done   │ 12m 01s  │ 89      │ 3h ago  │  │
│  │ sess_ghi56  │ ◉ Active │ 2m 11s   │ 12      │ now     │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  [Load More]                                                     │
└──────────────────────────────────────────────────────────────────┘
```

**Behavior:**
- 4 status cards: system health, active session count, healthy worker count, capacity gauge (used/total slots)
- Status guidance card: contextual message about why the system is in its current state (e.g., "No workers available" or "Ready for sessions"), with expandable details
- Session history table with status filter and sort
- Mobile: switches to card layout
- Delete action available on completed sessions (with confirmation)
- "Start Live Session" navigates to `/realtime/live`

---

### 3.8 Real-time Live Session

**Route:** `/realtime/live`
**Purpose:** Record and transcribe audio in real-time via microphone.

```
┌──────────────────────────────────────────────────────────────────┐
│  ← Back to Real-time                                             │
│  Live Transcription                                              │
│                                                                  │
│  ┌─────────────────────────────────────────────┐                │
│  │  Settings                                    │                │
│  │  Language: [Auto ▼]  Model: [auto ▼]         │                │
│  │  Vocabulary: [optional tags]                  │                │
│  └─────────────────────────────────────────────┘                │
│                                                                  │
│  ┌─────────────────────────────────────────────┐                │
│  │                                             │                │
│  │  Audio Level:  ████████████░░░░░░░░░░░░░░   │                │
│  │                                             │                │
│  │          [● Start Recording]                │                │
│  │     or   [■ Stop]  (when recording)         │                │
│  │                                             │                │
│  │  Duration: 02:34    Words: 156              │                │
│  └─────────────────────────────────────────────┘                │
│                                                                  │
│  ┌─────────────────────────────────────────────┐                │
│  │  Live Transcript                             │                │
│  │  ─────────────                               │                │
│  │  00:00  Hello, welcome to the meeting.       │                │
│  │  00:05  Let's discuss the quarterly results. │                │
│  │  00:12  Speaker 2: The numbers look good...  │                │
│  │  00:18  ▌ (partial — currently being spoken) │  ← auto-scroll│
│  └─────────────────────────────────────────────┘                │
└──────────────────────────────────────────────────────────────────┘
```

**Behavior:**
- Settings panel: language selector, model selector, optional vocabulary
- Audio level meter: real-time visualization of microphone input (0–100 bar)
- Start/Stop button toggles recording state
- State machine: idle → connecting → recording → stopping → completed/error
- **During recording:**
  - Audio captured via `getUserMedia` (browser microphone API)
  - Streamed over WebSocket to a real-time worker
  - Live transcript auto-scrolls with new segments
  - Partial text shown with a cursor indicator for currently-being-spoken words
  - Duration timer and word count update live
- **On stop:** Session completes, navigates to session detail page
- **On error:** Error message displayed, session can be retried
- **Resource cleanup:** Audio context, media streams, WebSocket, and timers all cleaned up properly on unmount

---

### 3.9 Real-time Session Detail

**Route:** `/realtime/sessions/:id`
**Purpose:** Review a completed real-time session's transcript.

```
┌──────────────────────────────────────────────────────────────────┐
│  ← Back to Real-time                                             │
│  Session: sess_abc12                              ● Completed    │
│                                                                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐           │
│  │ Model    │ │ Duration │ │ Segments │ │ Retained │           │
│  │ large-v3 │ │ 5m 23s   │ │ 34       │ │ 7 days   │           │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘           │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  Transcript                         [SRT] [VTT] [TXT] [JSON]│
│  │  ──────────                                                │  │
│  │  00:00  Hello, welcome to the meeting.                     │  │
│  │  00:05  Let's discuss the quarterly results.               │  │
│  │  00:12  Speaker 2: The numbers look good this quarter.     │  │
│  │  ...                                                       │  │
│  │                     [▶ Audio Player] (if audio stored)     │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

**Behavior:**
- Same transcript viewer component as batch jobs (with speaker colors, PII toggle, export)
- Audio playback available if audio was stored
- Retention info showing purge countdown
- Export buttons for SRT, VTT, TXT, JSON downloads

---

### 3.10 Engines

**Route:** `/engines`
**Purpose:** View pipeline stages, engine health, and real-time worker status.

```
┌──────────────────────────────────────────────────────────────────┐
│  Engines & Workers                                               │
│                                                                  │
│  Batch Pipeline                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ Stage          │ Engine                │ Health │ Queue    │  │
│  │────────────────│──────────────────────│────────│─────────│  │
│  │ transcribe     │ faster-whisper-base   │ ●      │ 3       │  │
│  │ align          │ whisperx-align        │ ●      │ 0       │  │
│  │ diarize        │ pyannote-diarize      │ ●      │ 1       │  │
│  │ pii_detect     │ presidio-pii          │ ○      │ 0       │  │
│  │ merge          │ final-merger          │ ●      │ 0       │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  Real-time Workers                                               │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ Worker ID     │ Model      │ Status  │ Sessions           │  │
│  │───────────────│───────────│────────│───────────────────│  │
│  │ worker-01     │ large-v3   │ ● Idle  │ 0/2               │  │
│  │ worker-02     │ medium     │ ● Busy  │ 2/2               │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

**Status dots:**
- Green (●): healthy / idle
- Yellow: warning / degraded
- Red: unhealthy / error
- Gray (○): empty / not configured

**Behavior:**
- Engines grouped by pipeline stage
- Queue depth shows pending items per engine
- Processing count shows currently active tasks
- Clicking an engine row navigates to Engine Detail
- Clicking a worker navigates to Worker Detail
- Real-time workers show session capacity (used/total)

---

### 3.11 Engine Detail

**Route:** `/engines/:id`
**Purpose:** Detailed view of a single engine's status, metrics, and loaded models.

**Content:**
- Engine metadata: ID, stage, runtime, health status
- Engine metrics: processing count, queue depth, uptime
- Loaded model name and status
- Available models list
- Model download management
- Capabilities display
- Load status indicators

---

### 3.12 Real-time Worker Detail

**Route:** `/realtime/workers/:id`
**Purpose:** View a specific real-time worker's metrics and model status.

**Content:**
- Worker ID, assigned model, health status
- Current session count and capacity
- Model loading status
- Worker-specific metrics

---

### 3.13 Models

**Route:** `/models`
**Purpose:** Model registry — download, manage, and monitor transcription models.

```
┌──────────────────────────────────────────────────────────────────┐
│  Models                                  [+ Add Model] [↻ Sync] │
│                                                                  │
│  [Search models...]  [Stage: All ▼]  [Status: All ▼]            │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ whisper-large-v3                          ● Ready  3.1 GB│   │
│  │ OpenAI Whisper Large V3                                  │   │
│  │ [faster-whisper] [transcribe]                            │   │
│  │ [timestamps] [punctuation] [GPU]                         │   │
│  │ 🌐 99 languages                                         │   │
│  │ [View on HF]                             [Remove]        │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ parakeet-tdt-1.1b                         ● Ready  1.1GB│   │
│  │ NeMo Parakeet TDT 1.1B                                  │   │
│  │ [nemo] [transcribe]                                      │   │
│  │ [timestamps] [CPU]                                       │   │
│  │ 🌐 en                                                   │   │
│  │ [View on HF]                             [Remove]        │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ whisper-medium                     ⬇ Downloading  1.5 GB│   │
│  │ ████████████░░░░░░░░░░░░░░  45%                         │   │
│  │ Downloading... 675 MB / 1.5 GB                           │   │
│  └──────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

**Model Card states:**

| State | Visual |
|---|---|
| Ready | Green badge, Remove action |
| Downloading | Yellow badge + spinner, progress bar with percentage and size |
| Not Downloaded | Gray badge, Pull (download) action |
| Failed | Red badge + error message, Retry action |

**Behavior:**
- Search field filters by model ID and name (300ms debounce)
- Filter dropdowns: Stage (transcribe, align, diarize...), Status (ready, downloading, etc.)
- **Actions:**
  - Pull: starts model download → status changes to "downloading" → polling updates progress
  - Remove: removes downloaded model files
  - Purge: removes model from registry entirely
  - Sync: re-scans disk to refresh model status
- **Add Model dialog:** Enter HuggingFace model ID → resolves engine compatibility → shows model info → "Add Model" button adds to registry
- Model compatibility warnings shown when engine/model mismatch detected
- Download progress polled every 5s; polling pauses when tab is hidden (Visibility API)
- Toast notification on download complete or failure
- Responsive: cards reflow on mobile

---

### 3.14 API Keys

**Route:** `/keys`
**Purpose:** Create, view, and revoke API keys with scope management.

```
┌──────────────────────────────────────────────────────────────────┐
│  API Keys                                      [+ Create Key]    │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ Name          │ Scopes              │ Created  │ Last Used │  │
│  │───────────────│────────────────────│─────────│──────────│  │
│  │ Production    │ [admin] [jobs:rw]   │ 30d ago  │ 2h ago   │  │
│  │ CI Pipeline   │ [jobs:read]         │ 14d ago  │ 1d ago   │  │
│  │ Mobile App    │ [realtime]          │ 7d ago   │ 5m ago   │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

**Scope badges (color-coded):**
- admin → red
- jobs:read → blue
- jobs:write → green
- realtime → purple
- webhooks → orange

**Create Key dialog:**
- Name input
- Scope selection (multi-select checkboxes)
- On create: **KeyCreatedModal** shows the full key one time only with a "Copy to clipboard" button
- Warning: "Save this key now — it won't be shown again"

**Behavior:**
- Revoke action per key (with confirmation dialog)
- Created/Last Used shown as relative time ("2h ago")
- Table supports responsive layout

---

### 3.15 Webhooks

**Route:** `/webhooks`
**Purpose:** Manage webhook endpoints that receive job event notifications.

```
┌──────────────────────────────────────────────────────────────────┐
│  Webhooks                                   [+ Create Webhook]   │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ URL                          │ Events        │ Status     │  │
│  │──────────────────────────────│──────────────│───────────│  │
│  │ https://api.example.com/hook │ completed,    │ ● Active  │  │
│  │                              │ failed        │           │  │
│  │ https://slack.webhook.site   │ * (all)       │ ● Active  │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

**Create Webhook dialog:**
- URL input
- Event filter (multi-select): transcription.completed, transcription.failed, transcription.cancelled, * (all)
- Active/Inactive toggle

**Behavior:**
- Edit and delete actions per webhook
- Secret rotation (generates new signing secret) with **WebhookSecretModal** showing new secret
- Clicking a webhook navigates to Webhook Detail

---

### 3.16 Webhook Detail

**Route:** `/webhooks/:id`
**Purpose:** View delivery history and retry failed deliveries for a webhook.

```
┌──────────────────────────────────────────────────────────────────┐
│  ← Back to Webhooks                                              │
│  Webhook: https://api.example.com/hook            ● Active       │
│                                                                  │
│  Events: [completed] [failed]                                    │
│                                                                  │
│  Delivery History                                                │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ Event              │ Status │ Response │ Time    │ Action  │  │
│  │────────────────────│────────│─────────│────────│────────│  │
│  │ transcription.done │ ✓ 200  │ OK       │ 1h ago │         │  │
│  │ transcription.done │ ✗ 500  │ Error    │ 3h ago │ [Retry] │  │
│  │ transcription.fail │ ✓ 200  │ OK       │ 5h ago │         │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

**Behavior:**
- Delivery log with HTTP status codes
- Retry button on failed deliveries
- Status icons: checkmark for 2xx, X for errors

---

### 3.17 Audit Log

**Route:** `/audit`
**Purpose:** System-wide audit trail of all actions and events.

```
┌──────────────────────────────────────────────────────────────────┐
│  Audit Log                                                       │
│                                                                  │
│  [Resource: All ▼]                                               │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ Time     │ Action      │ Resource        │ Actor          │  │
│  │──────────│────────────│────────────────│───────────────│  │
│  │ 14:02    │ ● created   │ job/abc123      │ key_prod       │  │
│  │ 13:55    │ ● exported  │ transcript/def  │ key_prod       │  │
│  │ 13:40    │ ● deleted   │ job/xyz789      │ key_ci         │  │
│  │ 12:30    │ ● created   │ session/ghi     │ key_mobile     │  │
│  │ 12:15    │ ● revoked   │ api_key/old     │ key_prod       │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  [Load More]                                                     │
└──────────────────────────────────────────────────────────────────┘
```

**Action badges (color-coded):**
- created → green
- deleted → red
- exported → purple
- updated → blue
- revoked → orange

**Behavior:**
- Resource type filter: job, transcript, audio, session, api_key, retention_policy
- Expandable rows showing full event details (JSON)
- Actor ID tracking (which API key performed the action)
- Resource links: clicking a job/session ID navigates to its detail page
- Pagination with "Load More"

---

### 3.18 Settings

**Route:** `/settings`
**Purpose:** View and modify system configuration organized by namespace.

```
┌──────────────────────────────────────────────────────────────────┐
│  Settings                                                        │
│                                                                  │
│  [Rate Limits] [Engines] [Audio] [Retention] [System]            │
│   ^^ active tab                                                  │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │ Setting                │ Value        │ Default │ Action   │  │
│  │────────────────────────│─────────────│────────│─────────│  │
│  │ Max requests/min       │ [100]        │ 60      │ [Reset] │  │
│  │ Max concurrent jobs    │ [10]         │ 5       │ [Reset] │  │
│  │ Rate limit window (s)  │ [60]         │ 60      │ [Reset] │  │
│  └────────────────────────────────────────────────────────────┘  │
│                                                                  │
│  [Save Changes]                                                  │
└──────────────────────────────────────────────────────────────────┘
```

**Namespace tabs:** rate_limits, engines, audio, retention, system (each with an icon)

**Behavior:**
- Type-specific input controls: integer/float number inputs, select dropdowns, text inputs
- Validation with min/max constraints
- Current value vs. default value display
- "Reset" button per setting to restore default
- Save applies all modified settings in the active namespace

---

## 4. Shared Components & Patterns

### 4.1 Status Badge

Used across all screens. Visual indicator with status-specific colors and optional pulse animation for "running" state.

| Status | Color | Animation |
|---|---|---|
| pending | gray bg, gray text | none |
| ready | yellow bg, yellow text | none |
| running | blue bg, blue text | pulse |
| completed | green bg, green text | none |
| failed | red bg, red text | none |
| skipped | light gray bg, gray text | none |
| cancelled | orange | none |

### 4.2 Transcript Viewer

Reused across Job Detail, Session Detail. Features:
- Time-indexed segments
- Speaker labels with 6-color rotation
- PII redaction toggle
- Export to SRT / VTT / TXT / JSON
- Virtual scrolling above 100 segments
- Click-to-seek integration with audio player
- Full-text view toggle

### 4.3 DAG Viewer

Pipeline visualization showing task dependencies. Used in Job Detail:
- Horizontal stage flow with connecting arrows
- Color-coded status nodes
- Duration and model labels
- Click-through to Task Detail

### 4.4 Confirmation Dialogs

Used for destructive actions (delete job, revoke key, remove model):
- Modal dialog with clear action description
- Cancel + Confirm buttons
- Confirm button uses destructive (red) variant

### 4.5 Toast Notifications

Non-blocking success/error messages:
- Appears at top-right
- Auto-dismisses after ~5 seconds
- Used for: job submitted, key created, model downloaded, delete/cancel confirmations, errors

### 4.6 Loading States

- **Skeleton loaders** used during initial data fetch (card-shaped placeholders)
- **staleTime: 30s** prevents skeleton flash on repeated navigation
- **Spinner** (Loader2 with spin animation) for inline loading states

### 4.7 Empty States

Contextual empty state messages with icons and CTAs when no data exists (no jobs, no models, no sessions, etc.).

### 4.8 Pagination

- **Cursor-based** for mutable data (jobs, sessions, audit log) — "Load More" button
- **Offset-based** for reference data (models, engines)

---

## 5. Responsive Behavior Summary

| Breakpoint | Width | Key Changes |
|---|---|---|
| Desktop | ≥1024px | Full tables, sidebar visible, multi-column grids |
| Tablet | 768–1023px | Tables drop some columns, sidebar visible, 2-col grids |
| Mobile | <768px | Cards replace tables, hamburger sidebar, single column, sticky action bars |

**Mobile-specific adaptations:**
- Table → Card list conversion for jobs, sessions
- Sidebar → Sheet drawer (slide from left)
- Sticky header with hamburger menu
- Bottom sticky action bars for forms
- Adjusted padding and touch target sizes (min 44px)
- Hidden secondary controls

---

## 6. Color System & Theming

Dark mode by default (class-based). All colors defined as HSL CSS variables.

| Token | Usage | Approximate Color |
|---|---|---|
| `--background` | Page background | Near-black (#0d1117) |
| `--foreground` | Primary text | Near-white |
| `--primary` | Interactive elements, links | Bright cyan-blue (#80d4ff) |
| `--destructive` | Delete buttons, error states | Red (#dc2626) |
| `--muted` | Disabled text, secondary info | Gray slate |
| `--accent` | Hover states, active nav | Dark blue-gray |
| `--border` | Card borders, dividers | Subtle gray |
| `--card` | Card backgrounds | Slightly lighter than background |

---

## 7. Data Refresh Strategy

| Screen | Refresh Approach |
|---|---|
| Dashboard | staleTime 30s, refetchOnWindowFocus |
| Job list | staleTime 30s, refetchOnWindowFocus |
| Job detail (running) | Polling every 2-5s while job is in-progress |
| Live session | Real-time WebSocket (no polling) |
| Models (downloading) | Polling every 5s while downloads active; pauses when tab hidden |
| Engines | staleTime 30s |
| Everything else | staleTime 30s, refetchOnWindowFocus |

---

## 8. Known Planned Improvements (M27 — Not Yet Implemented)

These features are specified but not yet built. Included for designer awareness:

1. **Slide-over detail panel** — Open job/session details in a side drawer instead of full-page navigation
2. **Search + date range filters** — Full-text search across jobs/transcripts with date pickers
3. **Audio-transcript sync** — Click a transcript segment to seek audio; highlight active segment during playback
4. **Timeline minimap** — Vertical minimap showing speaker distribution for long transcripts
5. **Keyboard shortcuts** — j/k navigation, Enter to open, Escape to close
6. **Bulk operations** — Select multiple jobs for batch delete/export

---

## 9. Navigation Flow Diagram

```
Login
  │
  ▼
Dashboard ──────────────────┐
  │                         │
  ├→ Batch Jobs ──────────→ Job Detail ──→ Task Detail
  │    └→ New Job             └→ (transcript, DAG, audio)
  │
  ├→ Real-time ───────────→ Session Detail
  │    └→ Live Session        └→ (transcript, audio)
  │
  ├→ Engines ─────────────→ Engine Detail
  │                        → Worker Detail
  │
  ├→ Models
  │
  ├→ API Keys
  │
  ├→ Webhooks ────────────→ Webhook Detail
  │
  ├→ Audit Log
  │
  └→ Settings
```

**Back navigation:** All detail pages have a "Back to [parent]" button. Browser back also works (React Router history).

---

## 10. Summary Table of All Screens

| # | Screen | Route | Purpose | Key Actions |
|---|---|---|---|---|
| 1 | Login | `/login` | API key auth | Enter key, sign in |
| 2 | Dashboard | `/` | System overview | View stats, navigate to recent items |
| 3 | Batch Jobs | `/jobs` | Job list | Filter, cancel, delete, navigate to detail |
| 4 | Submit Job | `/jobs/new` | Create job | Upload/URL, configure, submit |
| 5 | Job Detail | `/jobs/:id` | Job monitoring | View DAG, read transcript, play audio, export |
| 6 | Task Detail | `/jobs/:id/tasks/:id` | Task inspection | View inputs/outputs, artifacts |
| 7 | Real-time Sessions | `/realtime` | Session history | View capacity, filter, delete, start live |
| 8 | Live Session | `/realtime/live` | Record + transcribe | Configure, record, view live transcript |
| 9 | Session Detail | `/realtime/sessions/:id` | Review session | Read transcript, play audio, export |
| 10 | Engines | `/engines` | Pipeline health | View stages, queue depths, worker status |
| 11 | Engine Detail | `/engines/:id` | Engine metrics | View model, capabilities, health |
| 12 | Worker Detail | `/realtime/workers/:id` | Worker metrics | View sessions, model status |
| 13 | Models | `/models` | Model management | Search, pull, remove, add from HuggingFace |
| 14 | API Keys | `/keys` | Key management | Create, revoke, view scopes |
| 15 | Webhooks | `/webhooks` | Webhook management | Create, edit, delete, rotate secret |
| 16 | Webhook Detail | `/webhooks/:id` | Delivery history | View deliveries, retry failures |
| 17 | Audit Log | `/audit` | Activity trail | Filter by resource, expand details |
| 18 | Settings | `/settings` | Configuration | Edit by namespace, reset to defaults |
