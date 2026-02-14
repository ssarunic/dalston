import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/api/client'
import type { AuditListParams } from '@/api/types'

export function useAuditEvents(params: AuditListParams = {}) {
  return useQuery({
    queryKey: ['auditEvents', params],
    queryFn: () => apiClient.getAuditEvents(params),
  })
}

export function useResourceAuditTrail(resourceType: string, resourceId: string | undefined) {
  return useQuery({
    queryKey: ['auditTrail', resourceType, resourceId],
    queryFn: () => apiClient.getResourceAuditTrail(resourceType, resourceId!),
    enabled: !!resourceId,
  })
}
