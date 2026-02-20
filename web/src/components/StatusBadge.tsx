import { cn } from '@/lib/utils'
import type { JobStatus, RealtimeSessionStatus, TaskStatus } from '@/api/types'

interface StatusBadgeProps {
  status: JobStatus | TaskStatus | RealtimeSessionStatus
  className?: string
}

const statusStyles: Record<string, string> = {
  pending: 'bg-zinc-500/20 text-zinc-400',
  ready: 'bg-yellow-500/20 text-yellow-400',
  running: 'bg-blue-500/20 text-blue-400 animate-pulse',
  active: 'bg-blue-500/20 text-blue-400 animate-pulse',
  completed: 'bg-green-500/20 text-green-400',
  failed: 'bg-red-500/20 text-red-400',
  error: 'bg-red-500/20 text-red-400',
  cancelling: 'bg-amber-500/20 text-amber-400 animate-pulse',
  cancelled: 'bg-orange-500/20 text-orange-400',
  interrupted: 'bg-orange-500/20 text-orange-400',
  skipped: 'bg-zinc-500/20 text-zinc-400',
}

function formatStatusLabel(status: string): string {
  return status
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ')
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
      {formatStatusLabel(status)}
    </span>
  )
}
