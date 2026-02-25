import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '@/api/client'
import type { UpdateSettingsRequest } from '@/api/types'

export function useSettingsNamespaces() {
  return useQuery({
    queryKey: ['settings', 'namespaces'],
    queryFn: () => apiClient.getSettingsNamespaces(),
  })
}

export function useSettingsNamespace(namespace: string) {
  return useQuery({
    queryKey: ['settings', namespace],
    queryFn: () => apiClient.getSettingsNamespace(namespace),
    enabled: !!namespace,
  })
}

export function useUpdateSettings(namespace: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (request: UpdateSettingsRequest) =>
      apiClient.updateSettingsNamespace(namespace, request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] })
    },
  })
}

export function useResetSettings(namespace: string) {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: () => apiClient.resetSettingsNamespace(namespace),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['settings'] })
    },
  })
}
