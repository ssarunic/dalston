import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/api/client'
import { useAuth } from '@/contexts/AuthContext'
import { POLL_INTERVAL_ACTIVE_MS, QUERY_RETRY_COUNT } from '@/lib/queryTimings'

/**
 * Queue Board data hook (M86).
 *
 * Polls at the active-items cadence (2s) because this page is the
 * operational monitoring surface — operators expect near-real-time
 * updates when debugging an incident.
 *
 * The fetch is always enabled while authenticated. We don't disable
 * polling when no active jobs exist because the empty state needs to
 * notice when the first new job arrives without waiting for a manual
 * refresh.
 */
export function useQueueBoard() {
  const { isAuthenticated, isLoading: authLoading } = useAuth()

  const { data, isLoading, error, dataUpdatedAt } = useQuery({
    queryKey: ['queue-board'],
    queryFn: () => apiClient.getQueueBoard(),
    refetchInterval: POLL_INTERVAL_ACTIVE_MS,
    retry: QUERY_RETRY_COUNT,
    enabled: isAuthenticated && !authLoading,
  })

  return {
    data,
    isLoading,
    error: error ?? undefined,
    lastUpdatedAt: dataUpdatedAt,
  }
}
