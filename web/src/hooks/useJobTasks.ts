import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/api/client'

export function useJobTasks(jobId: string | undefined) {
  return useQuery({
    queryKey: ['job-tasks', jobId],
    queryFn: () => apiClient.getJobTasks(jobId!),
    enabled: !!jobId,
    retry: false,
    refetchInterval: 2000,
  })
}
