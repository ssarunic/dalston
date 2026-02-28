import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/api/client'

/**
 * Fetch the model catalog for batch transcription.
 * These are specific model variants (e.g., faster-whisper-large-v3-turbo)
 * that can be selected when creating a job.
 */
export function useModels(params: { stage?: string } = {}) {
  return useQuery({
    queryKey: ['models', params.stage],
    queryFn: () => apiClient.getModels({ stage: params.stage }),
    staleTime: 60000, // Cache for 1 minute
  })
}

/**
 * Fetch transcription models only.
 * Convenience hook for the NewJob form.
 */
export function useTranscriptionModels() {
  return useModels({ stage: 'transcribe' })
}
