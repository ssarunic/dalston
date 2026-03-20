# 06 — Task Detail

**Route:** `/jobs/:jobId/tasks/:taskId`
**Component:** `src/pages/TaskDetail.tsx`
**Auth required:** Yes

## Purpose

Inspect a single pipeline task within a job: status, timing metrics, dependencies, input request, and output data with stage-specific rendering.

## Storyboard

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│  [←]  TRANSCRIBE                    ● completed              │
│       Runtime: stt-transcribe-faster-whisper-base            │
│       Task: task_abc123                                      │
│                                                              │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│  │ Duration │ │ Started  │ │Completed │ │ Retries  │       │
│  │          │ │          │ │          │ │          │       │
│  │  45.2s   │ │ 3:45:12  │ │ 3:45:57  │ │  0 / 3   │       │
│  │          │ │  PM      │ │  PM      │ │ Required │       │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘       │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ Dependencies                                           │  │
│  │                                                        │  │
│  │  [● PREPARE] ───→ this task                           │  │
│  │                                                        │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ ▾ Input                                                │  │
│  │   {                                                    │  │
│  │     "audio_path": "s3://...",                         │  │
│  │     "language": "auto",                               │  │
│  │     "model": "Systran/faster-whisper-large-v3"        │  │
│  │   }                                              [📋] │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ ▾ Output                                               │  │
│  │                                                        │  │
│  │  Language: EN (98%)   Segments: 87                     │  │
│  │                                                        │  │
│  │  Full Text                                             │  │
│  │  ┌──────────────────────────────────────────────────┐  │  │
│  │  │ Welcome everyone to today's meeting. I'd like    │  │  │
│  │  │ to start by reviewing the quarterly results...   │  │  │
│  │  └──────────────────────────────────────────────────┘  │  │
│  │                                                        │  │
│  │  Segments (first 10)                                   │  │
│  │  0:00  Welcome everyone to today's meeting.            │  │
│  │  0:15  Thanks for organizing this.                     │  │
│  │  0:28  Let me share my screen...                       │  │
│  │  ... and 77 more segments                              │  │
│  │                                                        │  │
│  │  ─────────────────────────────                         │  │
│  │  [Show Raw JSON]                                       │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

## Sections

### 1. Header
- Back button (← to Job Detail)
- Stage name in uppercase bold
- Status pill (color-coded: green/blue/red/yellow/gray with icon)
- Runtime engine ID and task ID

### 2. Error Banner (conditional)
- Red card with AlertCircle icon
- Error message text
- Retry count if applicable

### 3. Metrics Grid (4 columns)
| Metric | Value |
|--------|-------|
| Duration | Formatted ms → "45.2s" |
| Started | Local time |
| Completed | Local time |
| Retries | "0 / 3" + "Required"/"Optional" subtext |

### 4. Dependencies Card
- Shows upstream task dependencies as clickable badges.
- Each badge: colored dot + stage name (uppercase), links to that task's detail.
- Arrow separator → "this task" label.

### 5. Input (collapsible)
- `<CollapsibleSection>` with chevron toggle.
- JSON viewer with syntax highlighting + copy button.
- Default: expanded for failed tasks, collapsed otherwise.

### 6. Output (collapsible)
- Default: expanded for completed tasks.
- **Stage-specific renderers:**
  - **Transcribe:** Language + confidence %, segment count, full text preview, first 10 segments with timestamps.
  - **Diarize:** Speaker list (chips), diarization segment count.
  - **Prepare:** Audio metrics (duration, channels, sample rate, split channels).
  - **Align:** Language, word timestamps yes/no, words aligned count + warning banner.
  - **Merge:** Language + confidence, duration, segment/speaker counts, pipeline stage chips, warnings, transcript preview.
  - **Other stages:** Raw JSON only.
- All views include "Show/Hide Raw JSON" toggle at bottom.

## Behaviour

- Data fetched via `useTaskArtifacts(jobId, taskId)` which returns task metadata + input/output.
- Job tasks list also fetched for dependency resolution.
- JSON viewer has one-click copy with green checkmark feedback.
- Loading state: blank (returns null).
- Error state: centered AlertCircle + "Back to Job" button.
