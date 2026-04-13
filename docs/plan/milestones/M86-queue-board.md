# M86: Queue Board — Cross-Job Pipeline Visualization

|                    |                                                              |
| ------------------ | ------------------------------------------------------------ |
| **Goal**           | Give operators a single live view of every active job and its tasks flowing through the pipeline, with switchable layouts for different debugging questions |
| **Duration**       | 3–5 days                                                     |
| **Dependencies**   | M33 (reliable task queues — complete), M78 (infrastructure topology — complete), M10 (web console — complete) |
| **Deliverable**    | `/api/console/queue-board` endpoint; `QueueBoard` page at `/queue` with three layouts (Grid, Stage Board, Job Strips) rendered by a shared `PivotBoard` component |
| **Status**         | Not Started                                                  |

## User Story

> *"As an operator debugging a production incident, I want to open one page and immediately see where every active job is in the pipeline, which stage is backed up, and which engine each task is running on — without clicking into individual jobs. When I'm chasing a different question I want to flip the same data into a different layout (per-stage kanban, per-job strips) without losing my place."*

---

## Outcomes

| Scenario | Current | After M86 |
| -------- | ------- | --------- |
| 12 jobs in flight, several stuck at transcribe | Must click each job individually to discover they're all blocked on the same stage | Open `/queue` — the transcribe column shows 12 pending tasks stacked up, bottleneck is visually obvious |
| Operator asks "which worker is handling job-abc123?" | Click job → scroll to task DAG → click task → read engine field | Find the card on the board, engine label is right on it |
| Job submitted with PII detection enabled | No easy way to see PII/redact tasks alongside regular transcription tasks across jobs | PII and audio_redact columns appear automatically; disappear when no jobs use them |
| Debugging "is whisperx-full stuck or just slow?" | Cannot easily see multiple tasks from the same engine across stages | In Grid view, the same engine name appears on three columns for that job — obvious at a glance |
| Operator wants a side-by-side view of 4 active jobs | No such view exists — Dashboard shows aggregate counts only | Switch to Job Strips layout — each job is a horizontal pipeline strip |

---

## Motivation

Dalston already has two strong observability surfaces:

- **`DAGViewer`** on the Job Detail page shows one job's task pipeline beautifully — but only one job.
- **Engines page** shows per-engine queue depths — but decoupled from jobs, so you can't see which jobs are waiting where.

Between these lies the operational question operators actually ask during an incident: *"What's happening right now across all the jobs in flight?"* Answering it today requires flipping between tabs, clicking into jobs one by one, and mentally joining the two views. That's slow when every second counts.

The Queue Board closes this gap with a cross-job, stage-aware, engine-annotated live view — and does so without duplicating either existing surface. The Job Detail page remains the place for deep single-job inspection, and the Engines page remains the place for per-engine health. The Queue Board is the *operational bridge* between them.

---

## Architecture

```
┌──────────────── Data Sources (existing) ────────────────┐
│                                                          │
│   PostgreSQL                       Redis Streams          │
│   ┌─────────────┐                  ┌─────────────────┐   │
│   │ jobs table  │                  │ dalston:stream: │   │
│   │ tasks table │                  │   {engine_id}   │   │
│   │ (eager-load │                  │ XLEN, XPENDING  │   │
│   │  tasks via  │                  │                 │   │
│   │selectinload)│                  │                 │   │
│   └──────┬──────┘                  └────────┬────────┘   │
│          │                                  │            │
└──────────┼──────────────────────────────────┼────────────┘
           │                                  │
           ▼                                  ▼
┌───────── ConsoleService.get_queue_board() ──────────────┐
│  1. Active jobs + tasks     (single eager-loaded query)  │
│  2. Per-stage queue depth   (aggregate XLEN across       │
│                              engines serving the stage)  │
│  3. Avg stage durations     (DB over last hour)          │
│  4. Compute visible_stages  (stages with non-skipped     │
│                              tasks across active jobs)   │
└───────────────────────────┬──────────────────────────────┘
                            │ JSON
                            ▼
┌──────────────── GET /api/console/queue-board ────────────┐
│  Flat task list + job list + stage health                │
└───────────────────────────┬──────────────────────────────┘
                            │
                            ▼
┌──────────────── Queue Board Page (/queue) ───────────────┐
│                                                           │
│  Summary cards    [active] [bottleneck] [tput] [avg]     │
│  View picker      [ Grid ] [ Stage Board ] [ Job Strips ] │
│                                                           │
│  ┌──────────────── <PivotBoard> ─────────────────────┐   │
│  │  props: groupByColumn='stage'|'none'              │   │
│  │         groupByRow='job'|'none'                   │   │
│  │                                                    │   │
│  │  Buckets tasks into row×col map, renders each     │   │
│  │  bucket as a stack of <TaskCard> elements.        │   │
│  └────────────────────────────────────────────────────┘   │
│                                                           │
│  Hidden stages:  align, audio_redact                      │
└───────────────────────────────────────────────────────────┘
```

---

## Steps

### 86.1: Queue Board Backend

**Files modified:**

- `dalston/gateway/api/console.py`
- `dalston/gateway/services/console.py`

**Deliverables:**

New endpoint `GET /api/console/queue-board` and service method `get_queue_board()`. The endpoint returns a **flat task list** (not nested by job+stage) because all three frontend layouts need to re-bucket the data in different ways, and a flat list is the most flexible shape.

```python
class QueueBoardTask(BaseModel):
    task_id: str
    job_id: str
    stage: str                  # normalized (no _ch0/_ch1 suffix)
    status: str                 # pending | ready | running | completed | failed | skipped
    engine_id: str | None
    duration_ms: int | None
    wait_ms: int | None
    started_at: str | None
    completed_at: str | None
    error: str | None

class QueueBoardJob(BaseModel):
    job_id: str
    display_name: str | None
    status: str                 # pending | running
    created_at: str
    audio_duration_seconds: float | None

class StageHealth(BaseModel):
    stage: str
    queue_depth: int            # sum of XLEN across engines serving this stage
    processing: int             # sum of XPENDING
    total_workers: int          # unique consumers across engines
    avg_duration_ms: float | None

class QueueBoardResponse(BaseModel):
    jobs: list[QueueBoardJob]
    tasks: list[QueueBoardTask]
    visible_stages: list[str]   # stages with at least one non-skipped task
    hidden_stages: list[str]    # known pipeline stages excluded from this view
    stages: list[StageHealth]   # health info only for visible stages
    completed_last_hour: int
    avg_pipeline_ms: float | None
```

**Service logic:**

1. Query DB for active jobs (`JobStatus.PENDING`, `JobStatus.RUNNING`) with `selectinload(Job.tasks)` — single round trip.
2. Flatten to a task list; normalize channel-suffixed stages (`transcribe_ch0` → `transcribe`), matching `DAGViewer.normalizeStage()`.
3. Compute `visible_stages`: the set of stages where at least one task has `status != SKIPPED`, ordered by the canonical pipeline order from `dalston/common/models.py`. The complement is `hidden_stages`.
4. For each visible stage, discover engines serving it (via existing engine registry scan) and sum `queue_depth` / `processing` / worker counts via the existing `_get_stream_backlog` helper used by `/api/console/engines`.
5. Query DB for `avg(completed_at - created_at)` over jobs completed in the last hour.

**N+1 safety:** the active-jobs query uses `selectinload`, stage health aggregation iterates the *set of distinct engines* (bounded by registry size, not by task count), and Redis calls are one per distinct engine — no per-task round trips.

---

### 86.2: PivotBoard Rendering Component

**Files modified:**

- `web/src/components/QueueBoard/PivotBoard.tsx` *(new)*
- `web/src/components/QueueBoard/TaskCard.tsx` *(new)*

**Deliverables:**

One rendering component that takes a flat task list and two grouping props, and produces any of the three layouts. The props are intentionally orthogonal so new permutations can be added later without restructuring.

```tsx
interface PivotBoardProps {
  tasks: QueueBoardTask[]
  jobs: QueueBoardJob[]
  visibleStages: string[]
  stageHealth: StageHealth[]
  groupByColumn: 'stage' | 'none'
  groupByRow: 'job' | 'none'
}
```

Internal bucketing is a `Map<rowKey, Map<colKey, QueueBoardTask[]>>`. Each bucket renders as a vertical stack of `<TaskCard>` elements.

- **Grid** (`groupByColumn='stage'`, `groupByRow='job'`) — HTML `<table>` with sticky header row and sticky first column. Empty cells render "—". Footer row shows `StageHealth` per visible stage; bottleneck column (highest `queue_depth`) gets an amber ring on the header.
- **Stage Board** (`groupByColumn='stage'`, `groupByRow='none'`) — flex row of stage columns, each with a vertical stack. Column headers show stage name and live queue depth; largest column = bottleneck.
- **Job Strips** (`groupByColumn='none'`, `groupByRow='job'`) — flex column of rows, each row a horizontal strip of cards ordered by canonical stage order.

`<TaskCard>` is shared across all three views. Props: `{ task, showStage, showJob }`. When `showStage` is false the stage label is hidden (Grid, Stage Board: the column implies it). When `showJob` is false the job ID is hidden (Grid, Job Strips: the row implies it). This is how the same card naturally adapts to each layout without per-view branches.

Card anatomy:

- Status dot (color from `DAGViewer` status palette)
- Stage label (conditional)
- Job ID monospace (conditional)
- Engine label — small, links to `/engines/:engineId` so operators can drill into per-engine metrics without losing their place
- Elapsed time for running tasks, computed from `started_at` at render time (updates naturally with the 2s poll cadence)
- Body of the card links to `/jobs/:jobId/tasks/:taskId`
- Failed cards: red ring + tooltip showing truncated error

**Flicker mitigation:** stable React keys on rows (`job_id`) and cards (`task_id`), and a CSS `transition` on column `flex-basis` / opacity when `visibleStages` changes.

---

### 86.3: ViewPicker + QueueBoard Page

**Files modified:**

- `web/src/components/QueueBoard/ViewPicker.tsx` *(new)*
- `web/src/pages/QueueBoard.tsx` *(new)*
- `web/src/hooks/useQueueBoard.ts` *(new)*
- `web/src/api/client.ts`
- `web/src/api/types.ts`

**Deliverables:**

**`ViewPicker`** — shadcn/ui `ToggleGroup` with three options using lucide-react icons: `Table2` (Grid), `Columns3` (Stage Board), `Rows3` (Job Strips). Emits a single `BoardView` string.

**`useQueueBoard`** — React Query hook that calls `getQueueBoard()`, polls at `POLL_INTERVAL_ACTIVE_MS` (2s — same as active job/task polling), and disables polling when the response has no active jobs (saves the gateway from needless load when the system is idle).

**`QueueBoard` page** — the page layout:

1. Title "Queue Board" + subtitle with active job count
2. Four summary cards matching the Dashboard pattern: active jobs, current bottleneck, completed in last hour, avg pipeline time
3. `<ViewPicker>` bound to URL query param `?view=grid|stage-board|job-strips` so operators can bookmark a preferred layout (default: `grid`)
4. `<PivotBoard>` with `groupByColumn` and `groupByRow` derived from the selected view
5. Hidden stages hint: a muted one-line label below the board, e.g. "Hidden: align, audio_redact — no active jobs use these stages". Only shown when `hidden_stages.length > 0`.
6. Empty state when `jobs.length === 0`: centered "All clear — no active jobs" with a link to `/jobs/new`

View-to-grouping mapping lives in a single `const VIEWS` record at the top of `QueueBoard.tsx`, so adding a fourth view later is a one-line change.

---

### 86.4: Navigation Integration

**Files modified:**

- `web/src/App.tsx`
- `web/src/components/Sidebar.tsx`

**Deliverables:**

- New route `{ path: '/queue', element: <QueueBoard /> }` in `App.tsx`
- New sidebar nav item `{ to: '/queue', icon: Kanban, label: 'Queue Board' }` inserted between "Batch Jobs" and "Real-time" (the Kanban icon from lucide-react is the most operationally-evocative choice)

No other pages change. The Job Detail and Engines pages remain the deep-dive surfaces; Queue Board is the operational bridge.

---

## Non-Goals

- **Historical replay / timeline scrubbing** — the Queue Board is a live view. Past job progression already lives in Job Detail and the audit log. A scrubber is a separate milestone if it proves needed.
- **Engine as a column axis** — queues are per-engine in Redis, but the many-to-many relationship between stages and engines (multi-transcriber fleets, multi-stage engines like whisperx-full) would make engine columns confusing. Engine information is surfaced on each card and links to the existing Engines page for drill-down.
- **Client-side filters / search** — the initial release has no filter bar. It's easy to add later using the same flat task list, but ships lean.
- **Row virtualization from day one** — `@tanstack/react-virtual` is already a dependency and can be wired in if production load justifies it. For typical 0–50 active jobs the native DOM performance is fine.
- **Realtime sessions** — this page covers batch jobs only. Realtime sessions have their own lifecycle (WebSocket, not Redis queue) and are already well-served by the Real-time page.
- **Editable state** — no cancel / retry / requeue actions directly from the board. Those live on Job Detail where the confirmation context is richer.

---

## Deployment

No ordering constraints. The endpoint is read-only and additive. Frontend and backend can deploy independently:

- If the frontend ships first and calls the endpoint before the backend is deployed, React Query will surface a 404 and the page will show its error state — harmless.
- If the backend ships first, the endpoint is simply unused until the frontend catches up.

No DB migrations. No Redis key changes. No config changes.

---

## Verification

```bash
make dev

# 1. Seed a few jobs with different configurations
dalston transcribe sample.wav &
dalston transcribe sample.wav --enable-pii &
dalston transcribe sample-stereo.wav &

# 2. Hit the endpoint directly
curl -s http://localhost:8000/api/console/queue-board \
  -H "Authorization: Bearer $DALSTON_API_KEY" \
  | jq '{job_count: (.jobs | length), task_count: (.tasks | length), visible_stages, hidden_stages, stage_health: .stages[] | {stage, queue_depth, processing}}'
# Expected: at least one job in jobs[], flat tasks[] with stage/status/engine_id per entry,
#           visible_stages ordered correctly, hidden_stages contains stages no active job uses

# 3. Open the Queue Board in the browser
open http://localhost:3000/queue
# Expected: Grid view by default, summary cards populated, rows = jobs, columns = visible stages

# 4. Switch views
open http://localhost:3000/queue?view=stage-board
# Expected: Stage Board layout — flex row of stage columns with cards stacked by queue depth

open http://localhost:3000/queue?view=job-strips
# Expected: Job Strips layout — one horizontal strip per job, cards ordered by canonical stage order

# 5. Verify hidden-stages hint
#    Submit a job with a whisperx-full transcriber (no explicit align task)
#    Expected: "Hidden: align" appears in the footer hint

# 6. Verify engine labels deep-link correctly
#    Click an engine label on any task card
#    Expected: navigates to /engines/:engineId

# 7. Verify 2s polling
#    Watch a running job's cards update in real-time with elapsed timers

# 8. Verify empty state
#    Wait for all jobs to complete, or run with no active jobs
#    Expected: "All clear — no active jobs" message with a CTA to /jobs/new
```

---

## Checkpoint

- [ ] `GET /api/console/queue-board` returns flat task list + job list + visible/hidden stages + per-stage health
- [ ] Backend query uses `selectinload` — no N+1 on tasks
- [ ] `visible_stages` reflects DAG reality (align hidden when transcribers have implicit alignment; PII/redact hidden when no jobs opted in)
- [ ] Channel-suffixed stages (`transcribe_ch0`) are normalized to their base stage in the response
- [ ] `PivotBoard` component renders all three layouts from the same `(tasks, jobs, visibleStages)` input
- [ ] `TaskCard` component adapts label visibility based on `showStage` / `showJob` props
- [ ] Grid view: sticky headers, per-stage footer, bottleneck ring on the busiest column
- [ ] Stage Board view: tallest column is visually obvious as the bottleneck
- [ ] Job Strips view: cards in canonical stage order per row
- [ ] View selection persists via `?view=` URL query param
- [ ] Hidden-stages hint appears below the board when any stages are excluded
- [ ] Engine label on each card links to `/engines/:engineId`
- [ ] Card body links to `/jobs/:jobId/tasks/:taskId`
- [ ] Page polls every 2s while jobs are active; disables polling when idle
- [ ] Empty state renders "All clear — no active jobs" with a CTA to `/jobs/new`
- [ ] Sidebar nav item present at `/queue` between "Batch Jobs" and "Real-time"
