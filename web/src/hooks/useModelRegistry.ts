import { useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '@/api/client'
import type { ModelFilters, HFResolveRequest } from '@/api/types'

/**
 * Fetch the model registry with optional filters.
 * Used for the Models page to show downloaded/available models.
 * Syncs with disk on first load to detect engine-downloaded models.
 * Automatically polls every 2s when any model is downloading.
 */
export function useModelRegistry(filters?: ModelFilters) {
  // Track if we've done the initial sync
  const hasSynced = useRef(false)

  return useQuery({
    queryKey: ['modelRegistry', filters],
    queryFn: () => {
      // Sync on first fetch to detect engine-downloaded models
      const shouldSync = !hasSynced.current
      hasSynced.current = true
      return apiClient.getModelRegistry(filters, { sync: shouldSync })
    },
    staleTime: 30_000,
    // Poll every 2s while any model is downloading
    refetchInterval: (query) => {
      const hasDownloading = query.state.data?.data?.some(m => m.status === 'downloading')
      return hasDownloading ? 2000 : false
    },
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
 * Remove a downloaded model's files (keeps registry entry).
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
 * Delete a model from the registry entirely.
 * Also removes downloaded files if present.
 * Invalidates the model registry query on success.
 */
export function usePurgeModel() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (modelId: string) => apiClient.purgeModel(modelId),
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
