import { useInfiniteQuery, useQuery } from '@tanstack/react-query'
import { apiClient } from '@/api/client'
import type { RealtimeSessionListParams } from '@/api/types'

type RealtimeSessionsFilters = Omit<RealtimeSessionListParams, 'cursor'>

export function useRealtimeSessions(params: RealtimeSessionsFilters = {}) {
  return useInfiniteQuery({
    queryKey: ['realtime-sessions', params],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) =>
      apiClient.getRealtimeSessions({
        ...params,
        cursor: typeof pageParam === 'string' ? pageParam : undefined,
      }),
    getNextPageParam: (lastPage) => (lastPage.has_more ? (lastPage.cursor ?? undefined) : undefined),
    refetchInterval: 5000,
  })
}

export function useRealtimeSession(sessionId: string | undefined) {
  return useQuery({
    queryKey: ['realtime-session', sessionId],
    queryFn: () => apiClient.getRealtimeSession(sessionId!),
    enabled: !!sessionId,
  })
}

export function useSessionTranscript(sessionId: string | undefined, enabled = true) {
  return useQuery({
    queryKey: ['session-transcript', sessionId],
    queryFn: () => apiClient.getSessionTranscript(sessionId!),
    enabled: !!sessionId && enabled,
  })
}
