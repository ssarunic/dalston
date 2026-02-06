import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/api/client'

export function useTaskArtifacts(jobId: string | undefined, taskId: string | undefined) {
  return useQuery({
    queryKey: ['task-artifacts', jobId, taskId],
    queryFn: () => apiClient.getTaskArtifacts(jobId!, taskId!),
    enabled: !!jobId && !!taskId,
    retry: false,
  })
}
