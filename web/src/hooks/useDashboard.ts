import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/api/client'
import { useAuth } from '@/contexts/AuthContext'

export function useDashboard() {
  const { isAuthenticated, isLoading: authLoading } = useAuth()

  const { data, isLoading, error } = useQuery({
    queryKey: ['dashboard'],
    queryFn: () => apiClient.getDashboard(),
    refetchInterval: 5000,
    retry: 1,
    enabled: isAuthenticated && !authLoading,
  })

  return {
    health: data
      ? { status: data.system.healthy ? 'healthy' : 'unhealthy', version: data.system.version }
      : undefined,
    jobStats: data
      ? {
          running: data.batch.running_jobs,
          queued: data.batch.queued_jobs,
          completed_today: data.batch.completed_today,
          failed_today: data.batch.failed_today,
        }
      : undefined,
    realtime: data
      ? {
          status: 'ready' as const,
          total_capacity: data.realtime.total_capacity,
          active_sessions: data.realtime.used_capacity,
          available_capacity: data.realtime.available_capacity,
          worker_count: data.realtime.worker_count,
          ready_workers: data.realtime.ready_workers,
        }
      : undefined,
    recentJobs: data?.recent_jobs ?? [],
    isLoading,
    error: error ?? undefined,
  }
}
