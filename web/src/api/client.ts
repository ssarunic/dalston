import ky, { type KyInstance } from 'ky'
import type {
  APIKeyCreatedResponse,
  APIKeyListResponse,
  AuditListParams,
  AuditListResponse,
  AudioUrlResponse,
  ConsoleJobListResponse,
  CreateAPIKeyRequest,
  CreateWebhookRequest,
  DashboardResponse,
  DeliveryListResponse,
  EnginesResponse,
  HealthResponse,
  JobDetail,
  JobStatsResponse,
  NamespaceSettings,
  RealtimeSessionDetail,
  RealtimeSessionListParams,
  RealtimeSessionListResponse,
  RealtimeStatusResponse,
  SessionTranscript,
  SettingsNamespaceListResponse,
  TaskArtifact,
  TaskListResponse,
  UpdateSettingsRequest,
  UpdateWebhookRequest,
  WebhookEndpointCreated,
  WebhookEndpointListResponse,
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

// Download file using authenticated fetch (avoids exposing API key in URLs)
async function authenticatedDownload(url: string, filename: string): Promise<void> {
  const headers: Record<string, string> = {}
  if (currentApiKey) {
    headers['Authorization'] = `Bearer ${currentApiKey}`
  }

  const response = await fetch(url, { headers })
  if (!response.ok) {
    throw new Error(`Download failed: ${response.status} ${response.statusText}`)
  }

  const blob = await response.blob()

  // Guard against empty exports (no transcript content)
  if (blob.size === 0) {
    throw new Error('No transcript content available to export')
  }

  const objectUrl = URL.createObjectURL(blob)

  const link = document.createElement('a')
  link.href = objectUrl
  link.download = filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)

  URL.revokeObjectURL(objectUrl)
}

export interface JobListParams {
  limit?: number
  cursor?: string
  status?: string
  sort?: 'created_desc' | 'created_asc'
}

export const apiClient = {
  // Health check (no auth required)
  getHealth: () => currentClient.get('health').json<HealthResponse>(),

  // Job stats (for dashboard)
  getJobStats: () => currentClient.get('v1/jobs/stats').json<JobStatsResponse>(),

  // Console dashboard (aggregated, consistent tenant filtering)
  getDashboard: () => currentClient.get('api/console/dashboard').json<DashboardResponse>(),

  // Jobs list - use console endpoint (admin required, shows all tenants)
  getJobs: (params: JobListParams = {}) => {
    const searchParams = new URLSearchParams()
    if (params.limit) searchParams.set('limit', String(params.limit))
    if (params.cursor) searchParams.set('cursor', params.cursor)
    if (params.status) searchParams.set('status', params.status)
    if (params.sort) searchParams.set('sort', params.sort)
    return currentClient.get('api/console/jobs', { searchParams }).json<ConsoleJobListResponse>()
  },

  // Job detail - use v1 endpoint (admin key has jobs:read scope, includes full transcript)
  getJob: (jobId: string) =>
    currentClient.get(`v1/audio/transcriptions/${jobId}`).json<JobDetail>(),

  // Tasks (admin required)
  getJobTasks: (jobId: string) =>
    currentClient.get(`api/console/jobs/${jobId}/tasks`).json<TaskListResponse>(),

  // Task artifacts (admin required)
  getTaskArtifacts: (jobId: string, taskId: string) =>
    currentClient.get(`api/console/jobs/${jobId}/tasks/${taskId}/artifacts`).json<TaskArtifact>(),

  // Realtime status (no auth required for basic status)
  getRealtimeStatus: () =>
    currentClient.get('v1/realtime/status').json<RealtimeStatusResponse>(),

  // Engines (admin required)
  getEngines: () =>
    currentClient.get('api/console/engines').json<EnginesResponse>(),

  // Delete a job (admin required, job must be in terminal state)
  deleteJob: (jobId: string) =>
    currentClient.delete(`api/console/jobs/${jobId}`),

  // Cancel a job (admin required, job must be pending or running)
  cancelJob: (jobId: string) =>
    currentClient.post(`api/console/jobs/${jobId}/cancel`).json<{ id: string; status: string; message: string }>(),

  // Download job export (uses authenticated fetch to avoid exposing API key in URLs)
  downloadJobExport: (jobId: string, format: 'srt' | 'vtt' | 'txt' | 'json') => {
    const url = `/v1/audio/transcriptions/${jobId}/export/${format}`
    const filename = `${jobId}.${format}`
    return authenticatedDownload(url, filename)
  },

  // Download session export (uses authenticated fetch to avoid exposing API key in URLs)
  downloadSessionExport: (sessionId: string, format: 'srt' | 'vtt' | 'txt' | 'json') => {
    const url = `/v1/realtime/sessions/${sessionId}/export/${format}`
    const filename = `${sessionId}.${format}`
    return authenticatedDownload(url, filename)
  },

  // Job audio URL (returns presigned S3 URL for download)
  getJobAudioUrl: (jobId: string) =>
    currentClient.get(`v1/audio/transcriptions/${jobId}/audio`).json<AudioUrlResponse>(),

  // Job redacted audio URL (returns presigned S3 URL for PII-redacted audio)
  getJobRedactedAudioUrl: (jobId: string) =>
    currentClient.get(`v1/audio/transcriptions/${jobId}/audio/redacted`).json<AudioUrlResponse>(),

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

  // API Key management
  getApiKeys: (includeRevoked = false) => {
    const searchParams = new URLSearchParams()
    if (includeRevoked) searchParams.set('include_revoked', 'true')
    return currentClient.get('auth/keys', { searchParams }).json<APIKeyListResponse>()
  },

  createApiKey: (request: CreateAPIKeyRequest) =>
    currentClient.post('auth/keys', { json: request }).json<APIKeyCreatedResponse>(),

  revokeApiKey: (keyId: string) =>
    currentClient.delete(`auth/keys/${keyId}`),

  // Webhook management
  getWebhooks: (isActive?: boolean) => {
    const searchParams = new URLSearchParams()
    if (isActive !== undefined) searchParams.set('is_active', String(isActive))
    return currentClient.get('v1/webhooks', { searchParams }).json<WebhookEndpointListResponse>()
  },

  createWebhook: (request: CreateWebhookRequest) =>
    currentClient.post('v1/webhooks', { json: request }).json<WebhookEndpointCreated>(),

  updateWebhook: (id: string, request: UpdateWebhookRequest) =>
    currentClient.patch(`v1/webhooks/${id}`, { json: request }).json<WebhookEndpointCreated>(),

  deleteWebhook: (id: string) =>
    currentClient.delete(`v1/webhooks/${id}`),

  rotateWebhookSecret: (id: string) =>
    currentClient.post(`v1/webhooks/${id}/rotate-secret`).json<WebhookEndpointCreated>(),

  getWebhookDeliveries: (
    endpointId: string,
    params: {
      status?: string
      sort?: 'created_desc' | 'created_asc'
      limit?: number
      cursor?: string
    } = {}
  ) => {
    const searchParams = new URLSearchParams()
    if (params.status) searchParams.set('status', params.status)
    if (params.sort) searchParams.set('sort', params.sort)
    if (params.limit) searchParams.set('limit', String(params.limit))
    if (params.cursor) searchParams.set('cursor', params.cursor)
    return currentClient
      .get(`v1/webhooks/${endpointId}/deliveries`, { searchParams })
      .json<DeliveryListResponse>()
  },

  retryWebhookDelivery: (endpointId: string, deliveryId: string) =>
    currentClient.post(`v1/webhooks/${endpointId}/deliveries/${deliveryId}/retry`).json(),

  // Realtime session management
  getRealtimeSessions: (params: RealtimeSessionListParams = {}) => {
    const searchParams = new URLSearchParams()
    if (params.status) searchParams.set('status', params.status)
    if (params.since) searchParams.set('since', params.since)
    if (params.until) searchParams.set('until', params.until)
    if (params.sort) searchParams.set('sort', params.sort)
    if (params.limit) searchParams.set('limit', String(params.limit))
    if (params.cursor) searchParams.set('cursor', params.cursor)
    return currentClient.get('v1/realtime/sessions', { searchParams }).json<RealtimeSessionListResponse>()
  },

  getRealtimeSession: (sessionId: string) =>
    currentClient.get(`v1/realtime/sessions/${sessionId}`).json<RealtimeSessionDetail>(),

  getSessionTranscript: (sessionId: string) =>
    currentClient.get(`v1/realtime/sessions/${sessionId}/transcript`).json<SessionTranscript>(),

  getSessionAudioUrl: (sessionId: string) =>
    currentClient.get(`v1/realtime/sessions/${sessionId}/audio`).json<{ url: string; expires_in: number }>(),

  deleteRealtimeSession: (sessionId: string) =>
    currentClient.delete(`v1/realtime/sessions/${sessionId}`).json<{ deleted: boolean; session_id: string }>(),

  // Audit log
  getAuditEvents: (params: AuditListParams = {}) => {
    const searchParams = new URLSearchParams()
    if (params.tenant_id) searchParams.set('tenant_id', params.tenant_id)
    if (params.action) searchParams.set('action', params.action)
    if (params.resource_type) searchParams.set('resource_type', params.resource_type)
    if (params.resource_id) searchParams.set('resource_id', params.resource_id)
    if (params.actor_id) searchParams.set('actor_id', params.actor_id)
    if (params.since) searchParams.set('start_time', params.since)
    if (params.until) searchParams.set('end_time', params.until)
    if (params.sort) searchParams.set('sort', params.sort)
    if (params.limit) searchParams.set('limit', String(params.limit))
    if (params.cursor) searchParams.set('cursor', params.cursor)
    return currentClient.get('v1/audit', { searchParams }).json<AuditListResponse>()
  },

  getResourceAuditTrail: (resourceType: string, resourceId: string) =>
    currentClient.get(`v1/audit/resources/${resourceType}/${resourceId}`).json<AuditListResponse>(),

  // Settings management
  getSettingsNamespaces: () =>
    currentClient.get('api/console/settings').json<SettingsNamespaceListResponse>(),

  getSettingsNamespace: (namespace: string) =>
    currentClient.get(`api/console/settings/${namespace}`).json<NamespaceSettings>(),

  updateSettingsNamespace: (namespace: string, request: UpdateSettingsRequest) =>
    currentClient.patch(`api/console/settings/${namespace}`, { json: request }).json<NamespaceSettings>(),

  resetSettingsNamespace: (namespace: string) =>
    currentClient.post(`api/console/settings/${namespace}/reset`).json<NamespaceSettings>(),
}
