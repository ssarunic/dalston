import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/api/client'

export function useRealtimeStatus() {
  return useQuery({
    queryKey: ['realtime-status'],
    queryFn: () => apiClient.getRealtimeStatus(),
    refetchInterval: 5000,
  })
}
