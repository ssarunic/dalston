import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/api/client'
import { POLL_INTERVAL_STANDARD_MS } from '@/lib/queryTimings'

export function useRealtimeStatus() {
  return useQuery({
    queryKey: ['realtime-status'],
    queryFn: () => apiClient.getRealtimeStatus(),
    refetchInterval: POLL_INTERVAL_STANDARD_MS,
  })
}
