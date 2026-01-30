import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/api/client'

export function useJob(jobId: string | undefined) {
  return useQuery({
    queryKey: ['job', jobId],
    queryFn: () => apiClient.getJob(jobId!),
    enabled: !!jobId,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status === 'pending' || status === 'running' ? 2000 : false
    },
  })
}
