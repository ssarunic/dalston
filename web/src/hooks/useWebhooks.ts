import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '@/api/client'
import type { CreateWebhookRequest, UpdateWebhookRequest } from '@/api/types'

export function useWebhooks(isActive?: boolean) {
  return useQuery({
    queryKey: ['webhooks', { isActive }],
    queryFn: () => apiClient.getWebhooks(isActive),
  })
}

export function useCreateWebhook() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (request: CreateWebhookRequest) => apiClient.createWebhook(request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['webhooks'] })
    },
  })
}

export function useUpdateWebhook() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ id, request }: { id: string; request: UpdateWebhookRequest }) =>
      apiClient.updateWebhook(id, request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['webhooks'] })
    },
  })
}

export function useDeleteWebhook() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (id: string) => apiClient.deleteWebhook(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['webhooks'] })
    },
  })
}

export function useRotateWebhookSecret() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (id: string) => apiClient.rotateWebhookSecret(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['webhooks'] })
    },
  })
}

export function useWebhookDeliveries(
  endpointId: string,
  params: { status?: string; limit?: number; offset?: number } = {}
) {
  return useQuery({
    queryKey: ['webhookDeliveries', endpointId, params],
    queryFn: () => apiClient.getWebhookDeliveries(endpointId, params),
    enabled: !!endpointId,
  })
}

export function useRetryDelivery() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ endpointId, deliveryId }: { endpointId: string; deliveryId: string }) =>
      apiClient.retryWebhookDelivery(endpointId, deliveryId),
    onSuccess: (_, { endpointId }) => {
      queryClient.invalidateQueries({ queryKey: ['webhookDeliveries', endpointId] })
    },
  })
}
