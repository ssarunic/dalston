import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/api/client'

export function useCapabilities() {
  return useQuery({
    queryKey: ['capabilities'],
    queryFn: () => apiClient.getCapabilities(),
    staleTime: 60000, // Cache for 1 minute
  })
}

export function useEnginesList() {
  return useQuery({
    queryKey: ['engines-list'],
    queryFn: () => apiClient.getEnginesList(),
    staleTime: 60000, // Cache for 1 minute
  })
}

export interface SystemCapabilities {
  features: {
    speaker_diarization: boolean
    pii_detection: boolean
    streaming: boolean
  }
  engines_by_stage: Record<string, number>
  models_ready: number
  models_total: number
}

export function useSystemCapabilities() {
  return useQuery({
    queryKey: ['system-capabilities'],
    queryFn: async (): Promise<SystemCapabilities> => {
      const [capabilities, models] = await Promise.all([
        apiClient.getCapabilities(),
        apiClient.getModelRegistry(),
      ])

      const stages = capabilities.stages
      const modelsData = models.data ?? []

      // Derive features from capabilities
      const features = {
        speaker_diarization: 'diarize' in stages,
        pii_detection: 'pii_detect' in stages,
        streaming: stages.transcribe?.supports_streaming ?? false,
      }

      // Count engines per stage
      const engines_by_stage: Record<string, number> = {}
      for (const [stage, caps] of Object.entries(stages)) {
        engines_by_stage[stage] = caps.engines.length
      }

      // Count models
      const models_ready = modelsData.filter(m => m.status === 'ready').length
      const models_total = modelsData.length

      return {
        features,
        engines_by_stage,
        models_ready,
        models_total,
      }
    },
    staleTime: 60_000, // Cache for 1 minute
  })
}
