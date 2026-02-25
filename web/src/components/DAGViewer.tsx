import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { cn } from '@/lib/utils'
import type { Task, TaskStatus } from '@/api/types'

interface DAGViewerProps {
  tasks: Task[]
  jobId: string
  jobStatus?: string
  className?: string
  audioDurationSeconds?: number | null
  jobCreatedAt?: string
  jobCompletedAt?: string | null
}

function formatDurationMs(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  const secs = ms / 1000
  if (secs < 60) return `${secs.toFixed(1)}s`
  const mins = Math.floor(secs / 60)
  return `${mins}m ${(secs % 60).toFixed(0)}s`
}

type TaskDisplayStatus = TaskStatus | 'blocked'

const statusConfig: Record<TaskDisplayStatus, { bg: string; text: string; ring?: string }> = {
  pending: { bg: 'bg-zinc-500/20', text: 'text-zinc-400' },
  ready: { bg: 'bg-yellow-500/20', text: 'text-yellow-400', ring: 'ring-yellow-500/50' },
  running: { bg: 'bg-blue-500/20', text: 'text-blue-400', ring: 'ring-blue-500/50' },
  completed: { bg: 'bg-green-500/20', text: 'text-green-400' },
  failed: { bg: 'bg-red-500/20', text: 'text-red-400', ring: 'ring-red-500/50' },
  skipped: { bg: 'bg-zinc-500/10', text: 'text-zinc-500' },
  cancelled: { bg: 'bg-orange-500/20', text: 'text-orange-400' },
  blocked: { bg: 'bg-amber-500/10', text: 'text-amber-300', ring: 'ring-amber-500/40' },
}

const stageOrder = ['prepare', 'transcribe', 'align', 'diarize', 'pii_detect', 'audio_redact', 'refine', 'merge']

interface StageGroup {
  stage: string
  tasks: Task[]
}

function normalizeStage(stage: string): string {
  return stage.replace(/_ch\d+$/, '')
}

function stageOrderIndex(stage: string): number {
  const idx = stageOrder.indexOf(normalizeStage(stage))
  return idx === -1 ? 99 : idx
}

function TaskNode({
  task,
  jobId,
  displayStatus,
  blockedByStage,
}: {
  task: Task
  jobId: string
  displayStatus: TaskDisplayStatus
  blockedByStage?: string | null
}) {
  const config = statusConfig[displayStatus] || statusConfig.pending
  const isActive = displayStatus === 'running' || displayStatus === 'ready'
  const dotColorClass =
    displayStatus === 'completed'
      ? 'bg-green-400'
      : displayStatus === 'running'
        ? 'bg-blue-400'
        : displayStatus === 'ready'
          ? 'bg-yellow-400'
          : displayStatus === 'failed'
            ? 'bg-red-400'
            : displayStatus === 'blocked'
              ? 'bg-amber-400'
              : displayStatus === 'cancelled'
                ? 'bg-orange-400'
                : 'bg-zinc-400'

  return (
    <Link to={`/jobs/${jobId}/tasks/${task.id}`}>
      <div
        className={cn(
          'relative flex flex-col gap-1 px-3 py-2 rounded-lg border border-border',
          'min-w-[140px] transition-all cursor-pointer',
          'hover:border-primary/50 hover:shadow-md',
          config.bg,
          isActive && 'ring-2',
          config.ring,
          task.status === 'running' && 'animate-pulse'
        )}
      >
        {/* Status indicator dot */}
        <div className="flex items-center gap-2">
          <div
            className={cn(
              'w-2 h-2 rounded-full',
              dotColorClass
            )}
          />
          <span className={cn('text-xs font-semibold uppercase', config.text)}>
            {task.stage}
          </span>
        </div>

        {/* Engine ID */}
        <span className="text-[10px] text-muted-foreground truncate">
          {task.engine_id}
        </span>

        {/* Duration - always show to prevent layout shift */}
        <span className="text-[10px] text-muted-foreground h-[14px]">
          {task.duration_ms != null && task.duration_ms > 0
            ? formatDurationMs(task.duration_ms)
            : '\u00A0'}
        </span>

        {/* Error indicator */}
        {displayStatus === 'blocked' && blockedByStage ? (
          <span className="text-[10px] text-amber-300 truncate" title={`Blocked by ${blockedByStage}`}>
            Blocked by {blockedByStage}
          </span>
        ) : task.error ? (
          <span className="text-[10px] text-red-400 truncate" title={task.error}>
            Error
          </span>
        ) : null}
      </div>
    </Link>
  )
}

function Arrow() {
  return (
    <div className="flex items-center justify-center w-8 flex-shrink-0">
      <svg
        width="32"
        height="12"
        viewBox="0 0 32 12"
        fill="none"
        className="text-border"
      >
        <path
          d="M0 6H28M28 6L23 1M28 6L23 11"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </div>
  )
}

function StageColumn({
  stageGroup,
  jobId,
  isLast,
  getDisplayStatus,
  blockedByStage,
}: {
  stageGroup: StageGroup
  jobId: string
  isLast: boolean
  getDisplayStatus: (task: Task) => TaskDisplayStatus
  blockedByStage?: string | null
}) {
  return (
    <div className="flex items-center">
      <div className="flex flex-col gap-2">
        {stageGroup.tasks.map((task) => (
          <TaskNode
            key={task.id}
            task={task}
            jobId={jobId}
            displayStatus={getDisplayStatus(task)}
            blockedByStage={blockedByStage}
          />
        ))}
      </div>
      {!isLast && <Arrow />}
    </div>
  )
}

export function DAGViewer({
  tasks,
  jobId,
  jobStatus,
  className,
  audioDurationSeconds,
  jobCreatedAt,
  jobCompletedAt,
}: DAGViewerProps) {
  const [showWhyState, setShowWhyState] = useState(false)
  const isJobFailed = jobStatus === 'failed'
  const failedTask = useMemo(() => {
    if (!isJobFailed) return null
    const failed = tasks.filter((task) => task.status === 'failed')
    if (failed.length === 0) return null
    return failed.sort((a, b) => {
      const stageDiff = stageOrderIndex(a.stage) - stageOrderIndex(b.stage)
      if (stageDiff !== 0) return stageDiff
      const aTime = a.started_at ? new Date(a.started_at).getTime() : 0
      const bTime = b.started_at ? new Date(b.started_at).getTime() : 0
      return aTime - bTime
    })[0]
  }, [isJobFailed, tasks])
  const failedStage = failedTask ? normalizeStage(failedTask.stage) : null
  const failedStageIdx = failedTask ? stageOrderIndex(failedTask.stage) : -1
  const displayStatusByTaskId = useMemo(() => {
    const result: Record<string, TaskDisplayStatus> = {}
    for (const task of tasks) {
      const isDownstream =
        failedTask !== null &&
        stageOrderIndex(task.stage) > failedStageIdx
      const shouldMarkBlocked =
        isJobFailed &&
        isDownstream &&
        (task.status === 'pending' || task.status === 'ready')
      result[task.id] = shouldMarkBlocked ? 'blocked' : task.status
    }
    return result
  }, [tasks, isJobFailed, failedTask, failedStageIdx])

  // Calculate total processing time (wall clock) - must be before early return
  const totalTimeMs = useMemo(() => {
    if (!jobCreatedAt || !jobCompletedAt) return null
    const start = new Date(jobCreatedAt).getTime()
    const end = new Date(jobCompletedAt).getTime()
    return end - start
  }, [jobCreatedAt, jobCompletedAt])

  // Calculate speed ratio (audio duration / wall clock time)
  const speedRatio = useMemo(() => {
    if (!totalTimeMs || !audioDurationSeconds || totalTimeMs <= 0) return null
    const wallClockSeconds = totalTimeMs / 1000
    return audioDurationSeconds / wallClockSeconds
  }, [totalTimeMs, audioDurationSeconds])

  // Group tasks by stage
  const stageGroups: StageGroup[] = stageOrder
    .map((stage) => ({
      stage,
      tasks: tasks.filter(
        (t) => t.stage === stage || t.stage.startsWith(`${stage}_ch`)
      ),
    }))
    .filter((group) => group.tasks.length > 0)

  if (stageGroups.length === 0) {
    return (
      <div className={cn('text-sm text-muted-foreground py-4', className)}>
        No pipeline tasks available
      </div>
    )
  }

  // Calculate overall progress
  const totalTasks = tasks.length
  const completedTasks = tasks.filter((t) => displayStatusByTaskId[t.id] === 'completed').length
  const failedTasks = tasks.filter((t) => displayStatusByTaskId[t.id] === 'failed').length
  const blockedTasks = tasks.filter((t) => displayStatusByTaskId[t.id] === 'blocked').length
  const runningTasks = tasks.filter((t) => displayStatusByTaskId[t.id] === 'running').length

  return (
    <div className={cn('space-y-4', className)}>
      {/* Progress summary */}
      <div className="flex items-center justify-between gap-4 text-sm">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-green-400" />
            <span className="text-muted-foreground">
              {completedTasks}/{totalTasks} completed
            </span>
          </div>
        {runningTasks > 0 && (
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-blue-400 animate-pulse" />
            <span className="text-muted-foreground">
              {runningTasks} running
            </span>
          </div>
        )}
        {failedTasks > 0 && (
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-red-400" />
            <span className="text-red-400">
              {failedTasks} failed
            </span>
          </div>
        )}
        {blockedTasks > 0 && (
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-amber-400" />
            <span className="text-amber-300">
              {blockedTasks} blocked
            </span>
          </div>
        )}
        </div>

        {/* Total time + speed ratio */}
        {totalTimeMs != null && (
          <div className="flex items-center gap-3 text-muted-foreground">
            <span>Total: {formatDurationMs(totalTimeMs)}</span>
            {speedRatio != null && (
              <span className="text-primary font-medium">
                {speedRatio >= 1 ? `${speedRatio.toFixed(1)}x` : `${(1 / speedRatio).toFixed(1)}x slower`}
              </span>
            )}
          </div>
        )}
      </div>

      {/* Progress bar */}
      <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden">
        <div
          className={cn(
            'h-full transition-all duration-500',
            failedTasks > 0 ? 'bg-red-500' : 'bg-green-500'
          )}
          style={{ width: `${(completedTasks / totalTasks) * 100}%` }}
        />
      </div>

      {/* DAG visualization */}
      <div className="max-w-full overflow-x-auto py-2 px-1">
        <div className="flex w-max items-center gap-0">
          {stageGroups.map((group, idx) => (
            <StageColumn
              key={group.stage}
              stageGroup={group}
              jobId={jobId}
              isLast={idx === stageGroups.length - 1}
              getDisplayStatus={(task) => displayStatusByTaskId[task.id]}
              blockedByStage={failedStage}
            />
          ))}
        </div>
      </div>

      {isJobFailed && failedTask && (
        <div className="rounded-md border border-amber-500/30 bg-amber-500/5 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm text-amber-300">
              Job failed at <span className="font-semibold">{failedTask.stage}</span>.
              Downstream tasks are shown as blocked because they were not executed.
            </p>
            <button
              type="button"
              className="text-xs text-amber-200 underline"
              onClick={() => setShowWhyState((prev) => !prev)}
            >
              {showWhyState ? 'Hide why this state' : 'Why this state?'}
            </button>
          </div>
          {showWhyState && (
            <div className="mt-2 flex flex-wrap gap-3 text-xs text-amber-200">
              <Link to={`/jobs/${jobId}/tasks/${failedTask.id}`} className="underline">
                View failed task
              </Link>
              <Link to="/engines" className="underline">
                Check engine health
              </Link>
            </div>
          )}
        </div>
      )}

      {/* Legend */}
      <div className="flex flex-wrap gap-4 text-xs text-muted-foreground pt-2 border-t border-border">
        <div className="flex items-center gap-1.5">
          <div className="w-2 h-2 rounded-full bg-zinc-400" />
          <span>Pending</span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className="w-2 h-2 rounded-full bg-yellow-400" />
          <span>Ready</span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className="w-2 h-2 rounded-full bg-blue-400" />
          <span>Running</span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className="w-2 h-2 rounded-full bg-green-400" />
          <span>Completed</span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className="w-2 h-2 rounded-full bg-red-400" />
          <span>Failed</span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className="w-2 h-2 rounded-full bg-amber-400" />
          <span>Blocked</span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className="w-2 h-2 rounded-full bg-zinc-500" />
          <span>Skipped</span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className="w-2 h-2 rounded-full bg-orange-400" />
          <span>Cancelled</span>
        </div>
      </div>
    </div>
  )
}
