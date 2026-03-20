# 05 — Job Detail

**Route:** `/jobs/:jobId`
**Component:** `src/pages/JobDetail.tsx`
**Auth required:** Yes

## Purpose

Detailed view of a single batch transcription job: status, metadata, task pipeline DAG, transcript viewer with audio playback, and audit trail.

## Storyboard

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│  [←] meeting-transcript-2026             ● Completed         │
│      ab3c8f1d-e5a2-4b7c-9d1f · Created Mar 20, 3:45 PM     │
│                                                              │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────────┐ │
│  │Audio │ │ Lang │ │Words │ │ Segs │ │Speak │ │Retention │ │
│  │      │ │      │ │      │ │      │ │      │ │          │ │
│  │4m 32s│ │  EN  │ │1,247 │ │  87  │ │   3  │ │ 30 days  │ │
│  └──────┘ └──────┘ └──────┘ └──────┘ └──────┘ │ 28d left │ │
│                                                └──────────┘ │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ Task Pipeline                                          │  │
│  │                                                        │  │
│  │  [PREPARE] ──→ [TRANSCRIBE] ──→ [ALIGN] ──→ [MERGE]  │  │
│  │    ✓ 2s         ✓ 45s            ✓ 3s       ✓ 1s     │  │
│  │              ╲                                         │  │
│  │               ──→ [DIARIZE] ────────────────↗          │  │
│  │                      ✓ 8s                              │  │
│  │                                                        │  │
│  │  Total: 59s  ·  RTF: 0.22x  ·  Model: whisper-large   │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ Transcript                                             │  │
│  │                                                        │  │
│  │  ▶ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━ 0:00 / 4:32     │  │
│  │  [Original ▾]  [⬇ Download ▾]  [📋 Export ▾]         │  │
│  │                                                        │  │
│  │  [Full Text] [Segments] [Speakers]                    │  │
│  │                                                        │  │
│  │  00:00  Welcome everyone to today's meeting. I'd like  │  │
│  │         to start by reviewing the quarterly results.   │  │
│  │  00:15  Speaker 2: Thanks for organizing this. The     │  │
│  │         numbers look really promising this quarter.    │  │
│  │  00:28  Speaker 1: Let me share my screen and walk     │  │
│  │         through the key highlights...                  │  │
│  │  ...                                                   │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ 📜 Audit Trail                                         │  │
│  │                                                        │  │
│  │  ● job.created     Mar 20, 3:45 PM   key_abc...       │  │
│  │  ● job.completed   Mar 20, 3:46 PM   system           │  │
│  │  ● transcript.accessed Mar 20, 3:47  key_abc...       │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### Failed Job Variant

```
  ┌────────────────────────────────────────────────────────┐
  │ ⚠ Job Failed                                           │
  │ ┌────────────────┐ ┌────────────────┐                  │
  │ │ ERROR          │ │ MESSAGE        │                  │
  │ │ EngineTimeout  │ │ Engine did not │                  │
  │ │                │ │ respond in 60s │                  │
  │ ├────────────────┤ ├────────────────┤                  │
  │ │ ENGINE         │ │ STAGE          │                  │
  │ │ whisper-large  │ │ transcribe     │                  │
  │ ├────────────────┤                                     │
  │ │ SUGGESTION     │ [View Raw JSON]                     │
  │ │ Try a smaller  │                                     │
  │ │ model or split │                                     │
  │ │ audio file     │                                     │
  │ └────────────────┘                                     │
  └────────────────────────────────────────────────────────┘
```

## Sections

### 1. Header
- Back button (← to `/jobs`)
- Job display name or ID as title
- StatusBadge (pill)
- Monospace job ID + creation timestamp

### 2. Failure Details (conditional, shown when `job.error` exists)
- Red-tinted card with `border-red-500/50`
- Parses error JSON (supports nested JSON extraction) into structured fields: Error, Message, Engine, Stage, Suggestion
- Each field in its own bordered sub-card
- "View Raw JSON" toggle reveals formatted JSON blob

### 3. Metadata Grid (6 columns on desktop)
| Card | Icon | Shows |
|------|------|-------|
| Audio | Mic | Duration (e.g. "4m 32.1s") |
| Language | Globe | Language code uppercase, or "Auto" |
| Words | FileText | Word count with locale formatting |
| Segments | FileText | Segment count |
| Speakers | Users | Speaker count |
| Retention | Archive | Retention policy + countdown/purge status |

### 4. Task Pipeline (`<DAGViewer>`)
- Visual DAG of pipeline tasks (prepare → transcribe → align/diarize → merge).
- Each node is clickable → navigates to `/jobs/:jobId/tasks/:taskId`.
- Shows task status (color-coded), duration, engine ID.
- Summary bar: total time, real-time factor (RTF), model used.

### 5. Transcript / Audio (`<TranscriptViewer>`)
- **Audio player:** HTML5 audio with presigned S3 URL, original + redacted variants.
- **Tab views:** Full Text / Segments / Speakers.
- **Segments view:** Timestamped segments with optional speaker labels.
- **Click-to-seek:** Clicking a segment seeks the audio player.
- **Export menu:** Download as SRT, VTT, TXT, JSON.
- **PII toggle:** If PII detection enabled, toggle between original and redacted text.
- Audio URLs are cached for 50 minutes, auto-refreshed before expiry.

### 6. Audit Trail
- Timeline of audit events for this job (created, completed, accessed, exported, etc.).
- Each event: action badge (color-coded) + timestamp + actor ID.

## Behaviour

- Job data polled via `useJob(jobId)` with auto-refetch while non-terminal.
- Tasks fetched separately via `useJobTasks(jobId)`.
- Audio URLs fetched lazily only for terminal, non-purged jobs.
- Shows blank (no skeleton) during initial load to avoid layout shift.
- Error state: centered AlertCircle icon with message + "Back to Jobs" button.
- Not found: centered "Job not found" + back button.
