import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/api/client'
import { POLL_INTERVAL_STANDARD_MS } from '@/lib/queryTimings'

export function useNodes() {
  return useQuery({
    queryKey: ['nodes'],
    queryFn: () => apiClient.getNodes(),
    refetchInterval: POLL_INTERVAL_STANDARD_MS * 2, // 10s — matches engine heartbeat interval
    retry: false,
  })
}
