import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '@/api/client'
import type { ModelFilters, HFResolveRequest } from '@/api/types'

/**
 * Fetch the model registry with optional filters.
 * Used for the Models page to show downloaded/available models.
 */
export function useModelRegistry(filters?: ModelFilters) {
  return useQuery({
    queryKey: ['modelRegistry', filters],
    queryFn: () => apiClient.getModelRegistry(filters),
    staleTime: 30_000, // Cache for 30 seconds
  })
}

/**
 * Fetch a single model registry entry by ID.
 */
export function useModelRegistryEntry(modelId: string) {
  return useQuery({
    queryKey: ['modelRegistry', modelId],
    queryFn: () => apiClient.getModelRegistryEntry(modelId),
    enabled: !!modelId,
  })
}

/**
 * Pull (download) a model from HuggingFace.
 * Invalidates the model registry query on success.
 */
export function usePullModel() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ modelId, force }: { modelId: string; force?: boolean }) =>
      apiClient.pullModel(modelId, force),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['modelRegistry'] })
    },
  })
}

/**
 * Remove a downloaded model.
 * Invalidates the model registry query on success.
 */
export function useRemoveModel() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (modelId: string) => apiClient.deleteModel(modelId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['modelRegistry'] })
    },
  })
}

/**
 * Resolve a HuggingFace model to determine compatible runtime.
 * Used for the "Add Model from HuggingFace" input.
 */
export function useResolveHFModel() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (request: HFResolveRequest) => apiClient.resolveHFModel(request),
    onSuccess: (data, variables) => {
      // If auto_register was true and resolution succeeded, refresh the registry
      if (variables.auto_register && data.can_route) {
        queryClient.invalidateQueries({ queryKey: ['modelRegistry'] })
      }
    },
  })
}

/**
 * Get HuggingFace library/tag to runtime mappings.
 * Used for debugging and displaying routing rules.
 */
export function useHFMappings() {
  return useQuery({
    queryKey: ['hfMappings'],
    queryFn: () => apiClient.getHFMappings(),
    staleTime: 300_000, // Cache for 5 minutes
  })
}

/**
 * Sync model registry with disk state.
 * Invalidates the model registry query on success.
 */
export function useSyncModels() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: () => apiClient.syncModels(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['modelRegistry'] })
    },
  })
}
