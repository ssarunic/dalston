import { useQueries } from '@tanstack/react-query'
import { apiClient } from '@/api/client'

export function useDashboard() {
  const results = useQueries({
    queries: [
      {
        queryKey: ['health'],
        queryFn: () => apiClient.getHealth(),
        refetchInterval: 10000,
        retry: false,
      },
      {
        queryKey: ['jobStats'],
        queryFn: () => apiClient.getJobStats(),
        refetchInterval: 5000,
        retry: false,
      },
      {
        queryKey: ['realtimeStatus'],
        queryFn: () => apiClient.getRealtimeStatus(),
        refetchInterval: 5000,
        retry: false,
      },
      {
        queryKey: ['recentJobs'],
        queryFn: () => apiClient.getJobs({ limit: 5 }),
        refetchInterval: 5000,
        retry: false,
      },
    ],
  })

  const [healthResult, jobStatsResult, realtimeResult, recentJobsResult] = results

  const isLoading = results.some((r) => r.isLoading)
  const error = results.find((r) => r.error)?.error

  return {
    health: healthResult.data,
    jobStats: jobStatsResult.data,
    realtime: realtimeResult.data,
    recentJobs: recentJobsResult.data?.jobs ?? [],
    isLoading,
    error,
  }
}
