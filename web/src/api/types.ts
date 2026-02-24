// Job types (matching backend responses)
export type JobStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelling' | 'cancelled'
export type TaskStatus = 'pending' | 'ready' | 'running' | 'completed' | 'failed' | 'skipped' | 'cancelled'

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
  // Result stats (populated on completion)
  audio_duration_seconds?: number
  result_language_code?: string
  result_word_count?: number
  result_segment_count?: number
  result_speaker_count?: number
}

export interface RetentionInfo {
  mode: 'auto_delete' | 'keep' | 'none'
  hours?: number
  purge_after?: string
  purged_at?: string
}

export interface PIIEntity {
  entity_type: string
  category: string
  start_offset: number
  end_offset: number
  start_time: number
  end_time: number
  confidence: number
  speaker?: string
  redacted_value: string
  original_text?: string
}

export interface PIIInfo {
  enabled: boolean
  detection_tier?: string
  entities_detected?: number
  entity_summary?: Record<string, number>
  redacted_audio_available: boolean
}

export interface JobDetail extends JobSummary {
  error?: string
  current_stage?: string
  language_code?: string
  text?: string
  words?: Word[]
  segments?: Segment[]
  speakers?: Speaker[]
  retention?: RetentionInfo
  // PII detection results (M26)
  pii?: PIIInfo
  redacted_text?: string
  entities?: PIIEntity[]
  // Additional result stats
  result_character_count?: number
}

export interface JobListResponse {
  jobs: JobSummary[]
  cursor: string | null
  has_more: boolean
}

// Console-specific types (admin endpoints)
export interface ConsoleJobSummary {
  id: string
  status: JobStatus
  model?: string
  audio_uri?: string
  created_at: string
  started_at?: string
  completed_at?: string
  // Result stats (populated on completion)
  audio_duration_seconds?: number
  result_language_code?: string
  result_word_count?: number
  result_segment_count?: number
  result_speaker_count?: number
}

export interface ConsoleJobListResponse {
  jobs: ConsoleJobSummary[]
  cursor: string | null
  has_more: boolean
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

// Audio URL response (for job audio download)
export interface AudioUrlResponse {
  url: string
  expires_in: number
  type: 'original' | 'redacted'
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
  disabled_reason: string | null
  consecutive_failures: number
  last_success_at: string | null
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
  cursor: string | null
  has_more: boolean
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

// Realtime Session types
export type RealtimeSessionStatus = 'active' | 'completed' | 'error' | 'interrupted'

export interface RealtimeSessionSummary {
  id: string
  status: RealtimeSessionStatus
  language: string | null
  model: string | null
  engine: string | null
  audio_duration_seconds: number
  segment_count: number
  word_count: number
  retention: number  // 0=transient, -1=permanent, N=days
  started_at: string
  ended_at: string | null
}

export interface RealtimeSessionDetail extends RealtimeSessionSummary {
  encoding: string | null
  sample_rate: number | null
  purge_after?: string
  purged_at?: string
  audio_uri: string | null
  transcript_uri: string | null
  worker_id: string | null
  client_ip: string | null
  previous_session_id: string | null
  error: string | null
}

export interface RealtimeSessionListResponse {
  sessions: RealtimeSessionSummary[]
  cursor: string | null
  has_more: boolean
}

export interface RealtimeSessionListParams {
  status?: string
  since?: string
  until?: string
  sort?: 'started_desc' | 'started_asc'
  limit?: number
  cursor?: string
}

export interface SessionUtterance {
  id: number
  start: number
  end: number
  text: string
}

export interface SessionTranscript {
  text: string
  utterances?: SessionUtterance[]
}

// Unified segment type for TranscriptViewer component
// Works with both batch Segment and realtime SessionUtterance
export interface UnifiedSegment {
  id: string | number
  start: number
  end: number
  text: string
  redacted_text?: string
  speaker?: string
  confidence?: number
}

// Audit Log types
export type AuditActorType = 'api_key' | 'system' | 'console_user' | 'webhook'

export interface AuditEvent {
  id: number
  timestamp: string
  correlation_id: string | null
  tenant_id: string | null
  actor_type: AuditActorType
  actor_id: string
  action: string
  resource_type: string
  resource_id: string
  detail: Record<string, unknown> | null
  ip_address: string | null
  user_agent: string | null
}

export interface AuditListResponse {
  events: AuditEvent[]
  cursor: string | null
  has_more: boolean
}

export interface AuditListParams {
  tenant_id?: string
  action?: string
  resource_type?: string
  resource_id?: string
  actor_id?: string
  since?: string
  until?: string
  sort?: 'timestamp_desc' | 'timestamp_asc'
  limit?: number
  cursor?: string
}
