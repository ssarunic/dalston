import { cn } from '@/lib/utils'
import type { JobStatus, TaskStatus } from '@/api/types'

interface StatusBadgeProps {
  status: JobStatus | TaskStatus
  className?: string
}

const statusStyles: Record<string, string> = {
  pending: 'bg-zinc-500/20 text-zinc-400',
  ready: 'bg-yellow-500/20 text-yellow-400',
  running: 'bg-blue-500/20 text-blue-400 animate-pulse',
  completed: 'bg-green-500/20 text-green-400',
  failed: 'bg-red-500/20 text-red-400',
  cancelling: 'bg-amber-500/20 text-amber-400 animate-pulse',
  cancelled: 'bg-orange-500/20 text-orange-400',
  skipped: 'bg-zinc-500/20 text-zinc-400',
}

export function StatusBadge({ status, className }: StatusBadgeProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2 py-1 text-xs font-medium',
        statusStyles[status] || statusStyles.pending,
        className
      )}
    >
      {status}
    </span>
  )
}
