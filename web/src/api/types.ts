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
