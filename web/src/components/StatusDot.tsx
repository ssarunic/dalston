import { cn } from '@/lib/utils'

export type DotStatus = 'healthy' | 'unhealthy' | 'warning' | 'empty'

const colors: Record<DotStatus, string> = {
  healthy: 'bg-green-500',
  unhealthy: 'bg-red-500',
  warning: 'bg-yellow-500',
  empty: 'bg-zinc-500',
}

export function StatusDot({ status }: { status: DotStatus }) {
  return <span className={cn('inline-block w-2 h-2 rounded-full shrink-0', colors[status])} />
}
