import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/api/client'

export function useCapabilities() {
  return useQuery({
    queryKey: ['capabilities'],
    queryFn: () => apiClient.getCapabilities(),
    staleTime: 60000, // Cache for 1 minute
  })
}

export function useEnginesList() {
  return useQuery({
    queryKey: ['engines-list'],
    queryFn: () => apiClient.getEnginesList(),
    staleTime: 60000, // Cache for 1 minute
  })
}
