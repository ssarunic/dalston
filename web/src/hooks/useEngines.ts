import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/api/client'

export function useEngines() {
  return useQuery({
    queryKey: ['engines'],
    queryFn: () => apiClient.getEngines(),
    refetchInterval: 10000,
    retry: false,
  })
}
