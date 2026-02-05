import { cn } from '@/lib/utils'
import type { Task, TaskStatus } from '@/api/types'

interface DAGViewerProps {
  tasks: Task[]
  className?: string
}

const statusConfig: Record<TaskStatus, { bg: string; text: string; ring?: string }> = {
  pending: { bg: 'bg-zinc-500/20', text: 'text-zinc-400' },
  ready: { bg: 'bg-yellow-500/20', text: 'text-yellow-400', ring: 'ring-yellow-500/50' },
  running: { bg: 'bg-blue-500/20', text: 'text-blue-400', ring: 'ring-blue-500/50' },
  completed: { bg: 'bg-green-500/20', text: 'text-green-400' },
  failed: { bg: 'bg-red-500/20', text: 'text-red-400', ring: 'ring-red-500/50' },
  skipped: { bg: 'bg-zinc-500/10', text: 'text-zinc-500' },
}

const stageOrder = ['prepare', 'transcribe', 'align', 'diarize', 'detect', 'refine', 'merge']

interface StageGroup {
  stage: string
  tasks: Task[]
}

function TaskNode({ task }: { task: Task }) {
  const config = statusConfig[task.status] || statusConfig.pending
  const isActive = task.status === 'running' || task.status === 'ready'

  return (
    <div
      className={cn(
        'relative flex flex-col gap-1 px-3 py-2 rounded-lg border border-border',
        'min-w-[140px] transition-all',
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
            task.status === 'completed' && 'bg-green-400',
            task.status === 'running' && 'bg-blue-400',
            task.status === 'ready' && 'bg-yellow-400',
            task.status === 'failed' && 'bg-red-400',
            task.status === 'pending' && 'bg-zinc-400',
            task.status === 'skipped' && 'bg-zinc-500'
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

      {/* Error indicator */}
      {task.error && (
        <span className="text-[10px] text-red-400 truncate" title={task.error}>
          Error
        </span>
      )}
    </div>
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

function StageColumn({ stageGroup, isLast }: { stageGroup: StageGroup; isLast: boolean }) {
  return (
    <div className="flex items-center">
      <div className="flex flex-col gap-2">
        {stageGroup.tasks.map((task) => (
          <TaskNode key={task.id} task={task} />
        ))}
      </div>
      {!isLast && <Arrow />}
    </div>
  )
}

export function DAGViewer({ tasks, className }: DAGViewerProps) {
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
  const completedTasks = tasks.filter((t) => t.status === 'completed').length
  const failedTasks = tasks.filter((t) => t.status === 'failed').length
  const runningTasks = tasks.filter((t) => t.status === 'running').length

  return (
    <div className={cn('space-y-4', className)}>
      {/* Progress summary */}
      <div className="flex items-center gap-4 text-sm">
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
      <div className="flex items-center gap-0 overflow-x-auto py-2 px-1">
        {stageGroups.map((group, idx) => (
          <StageColumn
            key={group.stage}
            stageGroup={group}
            isLast={idx === stageGroups.length - 1}
          />
        ))}
      </div>

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
          <div className="w-2 h-2 rounded-full bg-zinc-500" />
          <span>Skipped</span>
        </div>
      </div>
    </div>
  )
}
