import { useInfiniteQuery, useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '@/api/client'
import type { CreateWebhookRequest, UpdateWebhookRequest } from '@/api/types'

type WebhookDeliveryParams = { status?: string; limit?: number; cursor?: string }
type WebhookDeliveryFilters = Omit<WebhookDeliveryParams, 'cursor'>

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
  params: WebhookDeliveryFilters = {}
) {
  return useInfiniteQuery({
    queryKey: ['webhookDeliveries', endpointId, params],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      apiClient.getWebhookDeliveries(endpointId, {
        ...params,
        cursor: typeof pageParam === 'string' ? pageParam : undefined,
      }),
    getNextPageParam: (lastPage) => (lastPage.has_more ? (lastPage.cursor ?? undefined) : undefined),
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
