import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '@/api/client'
import type { CreateAPIKeyRequest } from '@/api/types'

export function useApiKeys(includeRevoked = false) {
  return useQuery({
    queryKey: ['apiKeys', { includeRevoked }],
    queryFn: () => apiClient.getApiKeys(includeRevoked),
  })
}

export function useCreateApiKey() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (request: CreateAPIKeyRequest) => apiClient.createApiKey(request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['apiKeys'] })
    },
  })
}

export function useRevokeApiKey() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (keyId: string) => apiClient.revokeApiKey(keyId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['apiKeys'] })
    },
  })
}
