import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/api/client'
import type { RealtimeSessionListParams } from '@/api/types'

export function useRealtimeSessions(params: RealtimeSessionListParams = {}) {
  return useQuery({
    queryKey: ['realtime-sessions', params],
    queryFn: () => apiClient.getRealtimeSessions(params),
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
