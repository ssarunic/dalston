import ky from 'ky'
import type {
  DashboardResponse,
  EnginesResponse,
  HealthResponse,
  JobDetail,
  JobListResponse,
  RealtimeStatusResponse,
  TaskListResponse,
} from './types'

// Base client with defaults
const api = ky.create({
  prefixUrl: '/',
  timeout: 30000,
  retry: 1,
})

export interface JobListParams {
  limit?: number
  offset?: number
  status?: string
}

export const apiClient = {
  // Health check
  getHealth: () => api.get('health').json<HealthResponse>(),

  // Dashboard (aggregated) - to be implemented in 10.6
  getDashboard: () => api.get('api/console/dashboard').json<DashboardResponse>(),

  // Jobs (reuse existing endpoints)
  getJobs: (params: JobListParams = {}) => {
    const searchParams = new URLSearchParams()
    if (params.limit) searchParams.set('limit', String(params.limit))
    if (params.offset) searchParams.set('offset', String(params.offset))
    if (params.status) searchParams.set('status', params.status)
    return api.get('v1/audio/transcriptions', { searchParams }).json<JobListResponse>()
  },

  getJob: (jobId: string) =>
    api.get(`v1/audio/transcriptions/${jobId}`).json<JobDetail>(),

  // Tasks (new endpoint for 10.6)
  getJobTasks: (jobId: string) =>
    api.get(`api/console/jobs/${jobId}/tasks`).json<TaskListResponse>(),

  // Realtime (reuse existing endpoints)
  getRealtimeStatus: () =>
    api.get('v1/realtime/status').json<RealtimeStatusResponse>(),

  // Engines
  getEngines: () =>
    api.get('api/console/engines').json<EnginesResponse>(),

  // Export
  getExportUrl: (jobId: string, format: 'srt' | 'vtt' | 'txt' | 'json') =>
    `/v1/audio/transcriptions/${jobId}/export/${format}`,
}
