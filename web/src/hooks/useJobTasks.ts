import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/api/client'
import type { TaskStatus } from '@/api/types'

const TERMINAL_STATUSES: TaskStatus[] = ['completed', 'failed', 'skipped', 'cancelled']

function hasNonTerminalTasks(tasks: Array<{ status: TaskStatus }> | undefined): boolean {
  if (!tasks || tasks.length === 0) return true // Keep polling until we have data
  return tasks.some((task) => !TERMINAL_STATUSES.includes(task.status))
}

export function useJobTasks(jobId: string | undefined) {
  return useQuery({
    queryKey: ['job-tasks', jobId],
    queryFn: () => apiClient.getJobTasks(jobId!),
    enabled: !!jobId,
    retry: false,
    refetchInterval: (query) => {
      // Only poll if there are non-terminal tasks
      return hasNonTerminalTasks(query.state.data?.tasks) ? 2000 : false
    },
  })
}
