import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '@/api/client'
import type { CreateRetentionPolicyRequest } from '@/api/types'

export function useRetentionPolicies() {
  return useQuery({
    queryKey: ['retentionPolicies'],
    queryFn: () => apiClient.getRetentionPolicies(),
  })
}

export function useCreateRetentionPolicy() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (request: CreateRetentionPolicyRequest) => apiClient.createRetentionPolicy(request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['retentionPolicies'] })
    },
  })
}

export function useDeleteRetentionPolicy() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (policyId: string) => apiClient.deleteRetentionPolicy(policyId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['retentionPolicies'] })
    },
  })
}
