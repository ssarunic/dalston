import ky, { type KyInstance } from 'ky'
import type {
  ConsoleJobListResponse,
  DashboardResponse,
  EnginesResponse,
  HealthResponse,
  JobDetail,
  RealtimeStatusResponse,
  TaskListResponse,
} from './types'

// Create a ky instance with optional auth
function createClient(apiKey?: string | null): KyInstance {
  const headers: Record<string, string> = {}
  if (apiKey) {
    headers['Authorization'] = `Bearer ${apiKey}`
  }

  return ky.create({
    prefixUrl: '/',
    timeout: 30000,
    retry: 1,
    headers,
  })
}

// Default client (no auth)
let currentClient = createClient()
let currentApiKey: string | null = null

// Update the client with a new API key
export function setApiKey(apiKey: string | null) {
  currentApiKey = apiKey
  currentClient = createClient(apiKey)
}

// Get current API key (for export URLs)
export function getApiKey(): string | null {
  return currentApiKey
}

export interface JobListParams {
  limit?: number
  offset?: number
  status?: string
}

export const apiClient = {
  // Health check (no auth required)
  getHealth: () => currentClient.get('health').json<HealthResponse>(),

  // Dashboard (admin required)
  getDashboard: () => currentClient.get('api/console/dashboard').json<DashboardResponse>(),

  // Jobs list - use console endpoint (admin required, shows all tenants)
  getJobs: (params: JobListParams = {}) => {
    const searchParams = new URLSearchParams()
    if (params.limit) searchParams.set('limit', String(params.limit))
    if (params.offset) searchParams.set('offset', String(params.offset))
    if (params.status) searchParams.set('status', params.status)
    return currentClient.get('api/console/jobs', { searchParams }).json<ConsoleJobListResponse>()
  },

  // Job detail - use v1 endpoint (admin key has jobs:read scope, includes full transcript)
  getJob: (jobId: string) =>
    currentClient.get(`v1/audio/transcriptions/${jobId}`).json<JobDetail>(),

  // Tasks (admin required)
  getJobTasks: (jobId: string) =>
    currentClient.get(`api/console/jobs/${jobId}/tasks`).json<TaskListResponse>(),

  // Realtime status (no auth required for basic status)
  getRealtimeStatus: () =>
    currentClient.get('v1/realtime/status').json<RealtimeStatusResponse>(),

  // Engines (admin required)
  getEngines: () =>
    currentClient.get('api/console/engines').json<EnginesResponse>(),

  // Export URL (needs API key as query param for download links)
  getExportUrl: (jobId: string, format: 'srt' | 'vtt' | 'txt' | 'json') => {
    const base = `/v1/audio/transcriptions/${jobId}/export/${format}`
    return currentApiKey ? `${base}?api_key=${currentApiKey}` : base
  },

  // Auth validation
  validateKey: async (apiKey: string): Promise<{ valid: boolean; isAdmin: boolean }> => {
    try {
      const response = await ky.get('auth/me', {
        prefixUrl: '/',
        headers: { 'Authorization': `Bearer ${apiKey}` },
      })
      const data = await response.json<{ scopes: string[] }>()
      return {
        valid: true,
        isAdmin: data.scopes?.includes('admin') ?? false,
      }
    } catch {
      return { valid: false, isAdmin: false }
    }
  },
}
