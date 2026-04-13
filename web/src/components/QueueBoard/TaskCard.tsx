import { Link } from 'react-router-dom'
import { cn } from '@/lib/utils'
import { Tooltip } from '@/components/ui/tooltip'
import { STAGE_LABELS } from '@/lib/stages'
import type { QueueBoardTask, TaskStatus } from '@/api/types'

/**
 * Compact task card used by all three queue board layouts (M87).
 *
 * The same component adapts to Grid / Stage Board / Job Strips by
 * toggling label visibility via `showStage` and `showJob`:
 *
 * - Grid view: showStage=false (column implies it), showJob=false (row implies it)
 * - Stage Board: showStage=false (column implies it), showJob=true
 * - Job Strips: showStage=true, showJob=false (row implies it)
 *
 * Clicking the card body navigates to the task detail page. Clicking
 * the small engine label deep-links to the engine page without
 * triggering the card's link (handled by stopPropagation + nested Link).
 */
interface TaskCardProps {
  task: QueueBoardTask
  showStage: boolean
  showJob: boolean
  /** Forces a compact style (used by Grid-mode cells to fit tightly). */
  compact?: boolean
  className?: string
}

const statusConfig: Record<
  TaskStatus,
  { dot: string; bg: string; border: string; text: string; label: string }
> = {
  pending: {
    dot: 'bg-zinc-500',
    bg: 'bg-zinc-900/60',
    border: 'border-zinc-700/60',
    text: 'text-zinc-400',
    label: 'Pending',
  },
  ready: {
    dot: 'bg-yellow-400',
    bg: 'bg-yellow-500/10',
    border: 'border-yellow-500/40',
    text: 'text-yellow-200',
    label: 'Queued',
  },
  running: {
    dot: 'bg-blue-400 animate-pulse',
    bg: 'bg-blue-500/15',
    border: 'border-blue-500/50',
    text: 'text-blue-100',
    label: 'Running',
  },
  completed: {
    dot: 'bg-green-400',
    bg: 'bg-green-500/10',
    border: 'border-green-500/40',
    text: 'text-green-200',
    label: 'Done',
  },
  failed: {
    dot: 'bg-red-400',
    bg: 'bg-red-500/10',
    border: 'border-red-500/60 ring-1 ring-red-500/40',
    text: 'text-red-200',
    label: 'Failed',
  },
  skipped: {
    dot: 'bg-slate-500',
    bg: 'bg-slate-900/40',
    border: 'border-slate-700/60',
    text: 'text-slate-500',
    label: 'Skipped',
  },
  cancelled: {
    dot: 'bg-orange-400',
    bg: 'bg-orange-500/10',
    border: 'border-orange-500/40',
    text: 'text-orange-200',
    label: 'Cancelled',
  },
}

/** Human-friendly duration in ms → "1.2s" / "12s" / "1m 30s". */
function formatMs(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  const secs = ms / 1000
  if (secs < 60) return `${secs.toFixed(1)}s`
  const mins = Math.floor(secs / 60)
  return `${mins}m ${Math.round(secs % 60)}s`
}

/** Elapsed wall-clock since a task started, as ms. */
function elapsedSinceMs(isoStart: string | null): number | null {
  if (!isoStart) return null
  const started = new Date(isoStart).getTime()
  if (Number.isNaN(started)) return null
  return Math.max(0, Date.now() - started)
}

function stageLabel(stage: string): string {
  return STAGE_LABELS[stage]?.label ?? stage
}

/** Short job id for card display. */
function shortJobId(jobId: string): string {
  return jobId.slice(0, 8)
}

export function TaskCard({
  task,
  showStage,
  showJob,
  compact = false,
  className,
}: TaskCardProps) {
  const config = statusConfig[task.status] ?? statusConfig.pending

  // For running tasks we show live-elapsed; for completed tasks we show
  // the persisted duration from the server.
  const displayMs =
    task.status === 'running'
      ? elapsedSinceMs(task.started_at)
      : task.duration_ms

  const taskHref = `/jobs/${task.job_id}/tasks/${task.task_id}`
  const engineHref = task.engine_id ? `/engines/${task.engine_id}` : null

  const body = (
    <div
      className={cn(
        'group flex flex-col gap-1 rounded-md border px-2.5 transition-colors',
        compact ? 'py-1.5 text-[11px]' : 'py-2 text-xs',
        config.bg,
        config.border,
        'hover:brightness-125',
        className,
      )}
    >
      {/* Header row: status dot + stage label (if shown) + duration */}
      <div className="flex items-center gap-1.5">
        <span
          className={cn('h-2 w-2 rounded-full shrink-0', config.dot)}
          aria-label={config.label}
        />
        {showStage && (
          <span className={cn('font-medium uppercase tracking-wide', config.text)}>
            {stageLabel(task.stage)}
          </span>
        )}
        {displayMs != null && (
          <span className={cn('ml-auto tabular-nums', config.text, 'opacity-80')}>
            {formatMs(displayMs)}
          </span>
        )}
      </div>

      {/* Secondary row: job id (if shown) */}
      {showJob && (
        <div className={cn('font-mono', config.text, 'opacity-80')}>
          {shortJobId(task.job_id)}
        </div>
      )}

      {/* Engine label — always shown; small but clickable. We render it as a
          nested Link and stopPropagation on click so the outer Link doesn't
          trigger. */}
      {task.engine_id && engineHref && (
        <Link
          to={engineHref}
          onClick={(e) => e.stopPropagation()}
          className={cn(
            'truncate text-[10px] font-mono underline-offset-2 hover:underline',
            config.text,
            'opacity-70 hover:opacity-100',
          )}
          title={`Engine: ${task.engine_id}`}
        >
          {task.engine_id}
        </Link>
      )}
    </div>
  )

  const card =
    task.status === 'failed' && task.error ? (
      <Tooltip content={<span className="block max-w-xs">{task.error}</span>} side="top">
        <Link to={taskHref} className="block">
          {body}
        </Link>
      </Tooltip>
    ) : (
      <Link to={taskHref} className="block">
        {body}
      </Link>
    )

  return card
}
