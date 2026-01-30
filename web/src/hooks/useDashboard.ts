import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/api/client'

export function useDashboard() {
  return useQuery({
    queryKey: ['dashboard'],
    queryFn: () => apiClient.getDashboard(),
    refetchInterval: 5000,
    retry: false,
  })
}
