import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import { cn } from '@/lib/utils'
import { STAGE_COLORS, STAGE_LABELS } from '@/lib/stages'
import type {
  QueueBoardJob,
  QueueBoardStageHealth,
  QueueBoardTask,
} from '@/api/types'
import { TaskCard } from './TaskCard'

/**
 * PivotBoard — one component that renders all three queue board layouts
 * by bucketing a flat task list along two orthogonal grouping axes.
 *
 * - groupByColumn: 'stage' | 'none'
 * - groupByRow:    'job'   | 'none'
 *
 * Combinations:
 *   stage × job  = Grid        (table)
 *   stage × none = Stage Board (Kanban by stage)
 *   none  × job  = Job Strips  (horizontal strip per job)
 *
 * The fourth combination (none × none) is intentionally unsupported —
 * a flat list duplicates the existing /jobs page.
 */
export type BoardColumnGrouping = 'stage' | 'none'
export type BoardRowGrouping = 'job' | 'none'

interface PivotBoardProps {
  tasks: QueueBoardTask[]
  jobs: QueueBoardJob[]
  visibleStages: string[]
  stageHealth: QueueBoardStageHealth[]
  groupByColumn: BoardColumnGrouping
  groupByRow: BoardRowGrouping
}

function stageLabel(stage: string): string {
  return STAGE_LABELS[stage]?.label ?? stage
}

function shortJobId(jobId: string): string {
  return jobId.slice(0, 8)
}

function formatMs(ms: number | null | undefined): string {
  if (ms == null) return '—'
  if (ms < 1000) return `${Math.round(ms)}ms`
  const secs = ms / 1000
  if (secs < 60) return `${secs.toFixed(1)}s`
  const mins = Math.floor(secs / 60)
  return `${mins}m ${Math.round(secs % 60)}s`
}

/** Group tasks by `task.stage`. Used by Grid and Stage Board. */
function groupByStage(
  tasks: QueueBoardTask[],
  visibleStages: string[],
): Map<string, QueueBoardTask[]> {
  const result = new Map<string, QueueBoardTask[]>()
  for (const stage of visibleStages) result.set(stage, [])
  for (const task of tasks) {
    const bucket = result.get(task.stage)
    if (bucket) bucket.push(task)
  }
  return result
}

/** Group tasks by `task.job_id`. Used by Grid and Job Strips. */
function groupByJob(tasks: QueueBoardTask[]): Map<string, QueueBoardTask[]> {
  const result = new Map<string, QueueBoardTask[]>()
  for (const task of tasks) {
    const bucket = result.get(task.job_id)
    if (bucket) {
      bucket.push(task)
    } else {
      result.set(task.job_id, [task])
    }
  }
  return result
}

/** Identify the bottleneck stage (highest queue_depth) if any stage has load. */
function findBottleneckStage(health: QueueBoardStageHealth[]): string | null {
  let max = 0
  let winner: string | null = null
  for (const h of health) {
    if (h.queue_depth > max) {
      max = h.queue_depth
      winner = h.stage
    }
  }
  return winner
}

export function PivotBoard(props: PivotBoardProps) {
  const { groupByColumn, groupByRow } = props

  if (groupByColumn === 'stage' && groupByRow === 'job') {
    return <GridLayout {...props} />
  }
  if (groupByColumn === 'stage' && groupByRow === 'none') {
    return <StageBoardLayout {...props} />
  }
  if (groupByColumn === 'none' && groupByRow === 'job') {
    return <JobStripsLayout {...props} />
  }
  // None/none is unsupported — fall through to Grid as a safe default.
  return <GridLayout {...props} />
}

// =============================================================================
// Grid Layout — rows = jobs, columns = stages
// =============================================================================

function GridLayout({
  tasks,
  jobs,
  visibleStages,
  stageHealth,
}: PivotBoardProps) {
  const bottleneck = findBottleneckStage(stageHealth)

  // Build a job×stage bucket map so each cell can render its task(s).
  const cellMap = useMemo(() => {
    const result = new Map<string, Map<string, QueueBoardTask[]>>()
    for (const task of tasks) {
      let row = result.get(task.job_id)
      if (!row) {
        row = new Map<string, QueueBoardTask[]>()
        result.set(task.job_id, row)
      }
      const stageBucket = row.get(task.stage)
      if (stageBucket) {
        stageBucket.push(task)
      } else {
        row.set(task.stage, [task])
      }
    }
    return result
  }, [tasks])

  const healthByStage = useMemo(() => {
    const map = new Map<string, QueueBoardStageHealth>()
    for (const h of stageHealth) map.set(h.stage, h)
    return map
  }, [stageHealth])

  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-card">
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr className="border-b border-border">
            <th className="sticky left-0 z-10 bg-card px-3 py-2 text-left font-medium text-muted-foreground">
              Job
            </th>
            {visibleStages.map((stage) => {
              const isBottleneck = bottleneck === stage
              return (
                <th
                  key={stage}
                  className={cn(
                    'px-2 py-2 text-left font-medium uppercase tracking-wide',
                    STAGE_COLORS[stage] ?? 'bg-zinc-500/20 text-zinc-300',
                    isBottleneck &&
                      'ring-1 ring-inset ring-amber-400/60',
                  )}
                >
                  {stageLabel(stage)}
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {jobs.length === 0 && (
            <tr>
              <td
                className="px-3 py-6 text-center text-muted-foreground"
                colSpan={visibleStages.length + 1}
              >
                No active jobs.
              </td>
            </tr>
          )}
          {jobs.map((job) => {
            const row = cellMap.get(job.job_id)
            return (
              <tr
                key={job.job_id}
                className="border-b border-border/60 last:border-b-0"
              >
                <td className="sticky left-0 z-10 bg-card px-3 py-2 align-top">
                  <Link
                    to={`/jobs/${job.job_id}`}
                    className="block font-mono text-xs text-foreground hover:underline"
                  >
                    {shortJobId(job.job_id)}
                  </Link>
                  {job.display_name && (
                    <div className="mt-0.5 max-w-[180px] truncate text-[10px] text-muted-foreground">
                      {job.display_name}
                    </div>
                  )}
                </td>
                {visibleStages.map((stage) => {
                  const cellTasks = row?.get(stage) ?? []
                  return (
                    <td key={stage} className="px-1.5 py-1.5 align-top">
                      {cellTasks.length === 0 ? (
                        <span className="block px-2 py-1 text-zinc-600">—</span>
                      ) : (
                        <div className="flex flex-col gap-1">
                          {cellTasks.map((task) => (
                            <TaskCard
                              key={task.task_id}
                              task={task}
                              showStage={false}
                              showJob={false}
                              compact
                            />
                          ))}
                        </div>
                      )}
                    </td>
                  )
                })}
              </tr>
            )
          })}
        </tbody>
        {jobs.length > 0 && (
          <tfoot>
            <tr className="border-t border-border bg-card/80">
              <td className="sticky left-0 z-10 bg-card px-3 py-2 text-[10px] uppercase tracking-wide text-muted-foreground">
                Stage health
              </td>
              {visibleStages.map((stage) => {
                const health = healthByStage.get(stage)
                const isBottleneck = bottleneck === stage
                return (
                  <td
                    key={stage}
                    className={cn(
                      'px-2 py-2 text-[10px] text-muted-foreground',
                      isBottleneck &&
                        'ring-1 ring-inset ring-amber-400/40',
                    )}
                  >
                    <div className="flex flex-col gap-0.5">
                      <span>
                        avg{' '}
                        <span className="tabular-nums text-foreground">
                          {formatMs(health?.avg_duration_ms)}
                        </span>
                      </span>
                      <span>
                        queue{' '}
                        <span className="tabular-nums text-foreground">
                          {health?.queue_depth ?? 0}
                        </span>
                      </span>
                      <span>
                        workers{' '}
                        <span className="tabular-nums text-foreground">
                          {health?.total_workers ?? 0}
                        </span>
                      </span>
                    </div>
                  </td>
                )
              })}
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  )
}

// =============================================================================
// Stage Board Layout — columns = stages, no rows
// =============================================================================

function StageBoardLayout({
  tasks,
  visibleStages,
  stageHealth,
}: PivotBoardProps) {
  const buckets = useMemo(
    () => groupByStage(tasks, visibleStages),
    [tasks, visibleStages],
  )
  const healthByStage = useMemo(() => {
    const map = new Map<string, QueueBoardStageHealth>()
    for (const h of stageHealth) map.set(h.stage, h)
    return map
  }, [stageHealth])
  const bottleneck = findBottleneckStage(stageHealth)

  return (
    <div className="flex gap-3 overflow-x-auto rounded-lg border border-border bg-card p-3">
      {visibleStages.map((stage) => {
        const stageTasks = buckets.get(stage) ?? []
        const health = healthByStage.get(stage)
        const isBottleneck = bottleneck === stage

        return (
          <div
            key={stage}
            className={cn(
              'flex min-w-[220px] max-w-[260px] flex-1 flex-col rounded-md border border-border/60 bg-background/40',
              isBottleneck && 'ring-1 ring-amber-400/60',
            )}
          >
            <div
              className={cn(
                'flex items-center justify-between rounded-t-md px-3 py-2 text-xs font-medium uppercase tracking-wide',
                STAGE_COLORS[stage] ?? 'bg-zinc-500/20 text-zinc-300',
              )}
            >
              <span>{stageLabel(stage)}</span>
              <span className="tabular-nums opacity-80">
                {health?.queue_depth ?? 0}
              </span>
            </div>
            <div className="flex flex-col gap-1.5 p-2">
              {stageTasks.length === 0 ? (
                <div className="py-4 text-center text-[10px] text-muted-foreground">
                  Idle
                </div>
              ) : (
                stageTasks.map((task) => (
                  <TaskCard
                    key={task.task_id}
                    task={task}
                    showStage={false}
                    showJob={true}
                  />
                ))
              )}
            </div>
            <div className="mt-auto border-t border-border/60 px-3 py-1.5 text-[10px] text-muted-foreground">
              avg {formatMs(health?.avg_duration_ms)} · workers{' '}
              {health?.total_workers ?? 0}
            </div>
          </div>
        )
      })}
    </div>
  )
}

// =============================================================================
// Job Strips Layout — rows = jobs, no columns (stage order is implicit)
// =============================================================================

function JobStripsLayout({ tasks, jobs, visibleStages }: PivotBoardProps) {
  const buckets = useMemo(() => groupByJob(tasks), [tasks])

  // Within each strip, sort tasks by pipeline stage order so the cards
  // read left-to-right the way they run.
  const orderOf = useMemo(() => {
    const map = new Map<string, number>()
    visibleStages.forEach((stage, idx) => map.set(stage, idx))
    return map
  }, [visibleStages])

  return (
    <div className="flex flex-col gap-2">
      {jobs.length === 0 && (
        <div className="rounded-lg border border-border bg-card px-4 py-6 text-center text-sm text-muted-foreground">
          No active jobs.
        </div>
      )}
      {jobs.map((job) => {
        const jobTasks = [...(buckets.get(job.job_id) ?? [])].sort((a, b) => {
          const ao = orderOf.get(a.stage) ?? 99
          const bo = orderOf.get(b.stage) ?? 99
          return ao - bo
        })

        return (
          <div
            key={job.job_id}
            className="flex items-stretch gap-3 rounded-lg border border-border bg-card p-3"
          >
            <div className="flex min-w-[160px] max-w-[200px] flex-col justify-center border-r border-border/60 pr-3">
              <Link
                to={`/jobs/${job.job_id}`}
                className="block font-mono text-xs text-foreground hover:underline"
              >
                {shortJobId(job.job_id)}
              </Link>
              {job.display_name && (
                <div className="mt-0.5 truncate text-[10px] text-muted-foreground">
                  {job.display_name}
                </div>
              )}
              <div className="mt-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
                {job.status}
              </div>
            </div>
            <div className="flex flex-wrap items-stretch gap-2">
              {jobTasks.length === 0 ? (
                <div className="py-2 text-[11px] text-muted-foreground">
                  No tasks yet
                </div>
              ) : (
                jobTasks.map((task) => (
                  <div key={task.task_id} className="min-w-[150px]">
                    <TaskCard task={task} showStage={true} showJob={false} />
                  </div>
                ))
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}
