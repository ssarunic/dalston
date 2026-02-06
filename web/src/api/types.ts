// Job types (matching backend responses)
export type JobStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
export type TaskStatus = 'pending' | 'ready' | 'running' | 'completed' | 'failed' | 'skipped'

export interface Word {
  word: string
  start: number
  end: number
  confidence: number
  speaker?: string
}

export interface Segment {
  id: string
  start: number
  end: number
  text: string
  speaker?: string
  confidence: number
  words?: Word[]
}

export interface Speaker {
  id: string
  label: string
  sample_start?: number
  sample_end?: number
}

export interface JobSummary {
  id: string
  status: JobStatus
  created_at: string
  started_at?: string
  completed_at?: string
  progress?: number
}

export interface JobDetail extends JobSummary {
  error?: string
  current_stage?: string
  language_code?: string
  text?: string
  words?: Word[]
  segments?: Segment[]
  speakers?: Speaker[]
}

export interface JobListResponse {
  jobs: JobSummary[]
  total: number
  limit: number
  offset: number
}

// Console-specific types (admin endpoints)
export interface ConsoleJobSummary {
  id: string
  status: JobStatus
  audio_uri?: string
  created_at: string
  started_at?: string
  completed_at?: string
}

export interface ConsoleJobListResponse {
  jobs: ConsoleJobSummary[]
  total: number
  limit: number
  offset: number
}

export interface ConsoleJobDetail {
  id: string
  status: JobStatus
  audio_uri?: string
  parameters?: Record<string, unknown>
  result?: Record<string, unknown>
  error?: string
  created_at: string
  started_at?: string
  completed_at?: string
}

// Task types
export interface Task {
  id: string
  stage: string
  engine_id: string
  status: TaskStatus
  dependencies: string[]
  started_at?: string
  completed_at?: string
  error?: string
}

export interface TaskListResponse {
  job_id: string
  tasks: Task[]
}

export interface TaskArtifact {
  task_id: string
  job_id: string
  stage: string
  engine_id: string
  status: TaskStatus
  required: boolean
  started_at?: string
  completed_at?: string
  duration_ms?: number
  retries: number
  max_retries: number
  error?: string
  dependencies: string[]
  input?: Record<string, unknown>
  output?: Record<string, unknown>
}

// Realtime types
export interface WorkerStatus {
  worker_id: string
  endpoint: string
  status: 'ready' | 'unhealthy'
  capacity: number
  active_sessions: number
  models: string[]
  languages: string[]
}

export interface CapacityInfo {
  total_capacity: number
  used_capacity: number
  available_capacity: number
  worker_count: number
  ready_workers: number
}

// Response from /v1/realtime/status (flat structure)
export interface RealtimeStatusResponse {
  status: 'ready' | 'at_capacity' | 'unavailable'
  total_capacity: number
  active_sessions: number
  available_capacity: number
  worker_count: number
  ready_workers: number
}

// Dashboard types (aggregated)
export interface SystemStatus {
  healthy: boolean
  version?: string
}

export interface BatchStats {
  running_jobs: number
  queued_jobs: number
  completed_today: number
  failed_today: number
}

export interface DashboardResponse {
  system: SystemStatus
  batch: BatchStats
  realtime: CapacityInfo
  recent_jobs: JobSummary[]
}

// Health check
export interface HealthResponse {
  status: string
  version?: string
}

// Job stats (for dashboard)
export interface JobStatsResponse {
  running: number
  queued: number
  completed_today: number
  failed_today: number
}

// API Key types
export interface APIKey {
  id: string
  prefix: string
  name: string
  tenant_id: string
  scopes: string[]
  rate_limit: number | null
  created_at: string
  last_used_at: string | null
  expires_at: string
  is_current: boolean
  is_revoked: boolean
}

export interface APIKeyListResponse {
  keys: APIKey[]
  total: number
}

export interface CreateAPIKeyRequest {
  name: string
  scopes?: string[]
  rate_limit?: number | null
}

export interface APIKeyCreatedResponse {
  id: string
  key: string
  prefix: string
  name: string
  tenant_id: string
  scopes: string[]
  rate_limit: number | null
  created_at: string
  expires_at: string
}

// Engine types
export interface BatchEngine {
  engine_id: string
  stage: string
  status: 'healthy' | 'unhealthy'
  queue_depth: number
  processing: number
}

export interface EnginesResponse {
  batch_engines: BatchEngine[]
  realtime_engines: WorkerStatus[]
}

// Webhook types
export interface WebhookEndpoint {
  id: string
  url: string
  events: string[]
  description: string | null
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface WebhookEndpointCreated extends WebhookEndpoint {
  signing_secret: string
}

export interface WebhookEndpointListResponse {
  endpoints: WebhookEndpoint[]
}

export interface WebhookDelivery {
  id: string
  endpoint_id: string | null
  job_id: string | null
  event_type: string
  status: 'pending' | 'success' | 'failed'
  attempts: number
  last_attempt_at: string | null
  last_status_code: number | null
  last_error: string | null
  created_at: string
}

export interface DeliveryListResponse {
  deliveries: WebhookDelivery[]
  total: number
  limit: number
  offset: number
}

export interface CreateWebhookRequest {
  url: string
  events: string[]
  description?: string | null
}

export interface UpdateWebhookRequest {
  url?: string
  events?: string[]
  description?: string | null
  is_active?: boolean
}
