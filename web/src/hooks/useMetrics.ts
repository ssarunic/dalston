import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/api/client'
import { useAuth } from '@/contexts/AuthContext'

export function useMetrics() {
  const { isAuthenticated, isLoading: authLoading } = useAuth()

  return useQuery({
    queryKey: ['metrics'],
    queryFn: () => apiClient.getMetrics(),
    refetchInterval: 30_000,
    retry: 1,
    enabled: isAuthenticated && !authLoading,
  })
}
