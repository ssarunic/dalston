import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { apiClient } from '@/api/client'
import type { AuditListParams, AuditListResponse } from '@/api/types'

type AuditEventFilters = Omit<AuditListParams, 'cursor'>

export function useAuditEvents(params: AuditEventFilters = {}) {
  return useInfiniteQuery<AuditListResponse>({
    queryKey: ['audit-events', params],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      apiClient.getAuditEvents({
        ...params,
        cursor: typeof pageParam === 'string' ? pageParam : undefined,
      }),
    getNextPageParam: (lastPage) => (lastPage.has_more ? (lastPage.cursor ?? undefined) : undefined),
    staleTime: 0,
  })
}

export function useResourceAuditTrail(resourceType: string, resourceId: string | undefined) {
  return useQuery({
    queryKey: ['auditTrail', resourceType, resourceId],
    queryFn: () => apiClient.getResourceAuditTrail(resourceType, resourceId!),
    enabled: !!resourceId,
  })
}
