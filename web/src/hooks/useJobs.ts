import { useQuery } from '@tanstack/react-query'
import { apiClient, type JobListParams } from '@/api/client'

export function useJobs(params: JobListParams = {}) {
  return useQuery({
    queryKey: ['jobs', params],
    queryFn: () => apiClient.getJobs(params),
    refetchInterval: 5000,
  })
}
