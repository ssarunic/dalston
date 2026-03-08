/**
 * Centralized UI strings for the Dalston console.
 *
 * Internal to the web/ package — do not import from backend or CLI code.
 *
 * Keeps magic strings out of components. If localization is ever needed,
 * these keys map directly to translation keys with a mechanical refactor.
 *
 * Usage:
 *
 *   import { S } from '@/lib/strings'
 *
 *   <CardTitle>{S.dashboard.title}</CardTitle>
 *   <p>{S.errors.failedToDelete('job')}</p>
 */

// ---------------------------------------------------------------------------
// Common / shared strings
// ---------------------------------------------------------------------------

const common = {
  // Actions
  cancel: 'Cancel',
  close: 'Close',
  delete: 'Delete',
  save: 'Save',
  saving: 'Saving...',
  refresh: 'Refresh',
  clear: 'Clear',
  login: 'Login',
  done: 'Done',
  loading: 'Loading...',
  loadMore: 'Load More',
  viewAll: 'View all',
  viewDetails: 'View details',
  copyToClipboard: 'Copy to clipboard',
  showKey: 'Show key',
  hideKey: 'Hide key',

  // Sort
  newestFirst: 'Newest first',
  oldestFirst: 'Oldest first',

  // Filters
  allStatuses: 'All Statuses',
  all: 'All',

  // Table columns
  colName: 'Name',
  colStatus: 'Status',
  colCreated: 'Created',
  colActions: 'Actions',
  colModel: 'Model',
  colDuration: 'Duration',

  // Status labels
  active: 'Active',
  inactive: 'Inactive',
  enabled: 'Enabled',
  disabled: 'Disabled',
  online: 'Online',
  offline: 'Offline',

  // Time
  justNow: 'just now',
  minutesAgo: 'm ago',
  hoursAgo: 'h ago',
  daysAgo: 'd ago',
  never: 'Never',

  // Misc
  current: 'current',
  revoked: 'revoked',
  required: 'Required',
  optional: 'Optional',
} as const

// ---------------------------------------------------------------------------
// Error & validation messages
// ---------------------------------------------------------------------------

const errors = {
  loginFailed: 'Login failed',
  invalidApiKey: 'Invalid API key',
  invalidUrlFormat: 'Invalid URL format',
  invalidCursorFormat: 'Invalid cursor format',

  nameRequired: 'Name is required',
  urlRequired: 'URL is required',
  valueRequired: 'Value is required',
  scopeRequired: 'At least one scope is required',
  eventRequired: 'At least one event is required',
  mustBeWholeNumber: 'Must be a whole number',
  mustBeNumber: 'Must be a number',
  minValue: (min: number) => `Minimum value is ${min}`,
  maxValue: (max: number) => `Maximum value is ${max}`,
  mustBeOneOf: (values: string) => `Must be one of: ${values}`,
  speakerRange: 'Must be between 1 and 32.',
  minLessOrEqualMax: 'Min must be less than or equal to max.',
  maxVocabulary: 'Maximum 100 vocabulary terms allowed.',

  selectAudioFile: 'Please select an audio file.',
  enterAudioUrl: 'Please enter an audio URL.',
  fixValidationErrors: 'Please fix validation errors before saving',
  fixErrorsBeforeSaving: 'Fix errors before saving',
  concurrencyConflict:
    'Settings were modified by another admin. Please refresh and try again.',

  failedToSubmitJob: 'Failed to submit job. Please try again.',
  failedToCreateKey: 'Failed to create key',
  failedToCreateWebhook: 'Failed to create webhook',
  failedToRevokeKey: 'Failed to revoke key',
  failedToSaveSettings: 'Failed to save settings',
  failedToResetSettings: 'Failed to reset settings',
  failedToLoadAudio: 'Failed to load audio',
  failedToDeleteJob: 'Failed to delete job',
  failedToCancelJob: 'Failed to cancel job',
  failedToDeleteSession: 'Failed to delete session',
  failedToLoadModels: 'Failed to load model registry',
  failedToLoadAudit: (error: string) => `Failed to load audit events: ${error}`,
  failedToLoadRealtimeStatus: 'Failed to load realtime status',

  cannotRevokeOwnKey: 'Cannot revoke the API key you are currently using',
} as const

// ---------------------------------------------------------------------------
// Page-specific strings
// ---------------------------------------------------------------------------

const login = {
  title: 'Dalston Console',
  instructions: 'Enter your API key with admin scope to access the console',
  apiKeyLabel: 'API Key',
  apiKeyPlaceholder: 'dk_...',
  validating: 'Validating...',
  createKeyHint: 'Create an admin key with:',
  createKeyCommand: 'python -m dalston.gateway.cli create-key --scopes admin',
} as const

const dashboard = {
  title: 'Dashboard',
  subtitle: 'System overview and recent activity',
  systemStatus: 'System Status',
  runningJobs: 'Running Jobs',
  realtimeSessions: 'Real-time Sessions',
  completedToday: 'Completed Today',
  queued: 'queued',
  workers: 'workers',
  failed: 'failed',
  noFailures: 'no failures',
  recentBatchJobs: 'Recent Batch Jobs',
  recentRealtimeSessions: 'Recent Real-time Sessions',
  noJobsYet: 'No jobs yet',
  noSessionsYet: 'No sessions yet',
} as const

const batchJobs = {
  title: 'Batch Jobs',
  subtitle: 'Manage and monitor transcription jobs',
  submitJob: 'Submit Job',
  cardTitle: 'Jobs',
  submitFirstJob: 'Submit your first job',
  noJobsFound: 'No jobs found matching the filter',
  noJobsYet: 'No jobs yet',
  segments: 'Segments',

  // Cancel dialog
  cancelJob: 'Cancel Job',
  cancelling: 'Cancelling...',
  cancelConfirm:
    'This will cancel the job. Running tasks will complete naturally, but no new tasks will be started.',

  // Delete dialog
  deleteJob: 'Delete Job',
  deleting: 'Deleting...',
  deleteConfirm:
    'This will permanently delete the job and all its artifacts (audio, transcripts, intermediate outputs). This action cannot be undone.',
} as const

const newJob = {
  // Language names stay close to the form — they're domain-specific constants
  // rather than UI chrome, but centralizing avoids duplication.
  languages: {
    en: 'English',
    es: 'Spanish',
    fr: 'French',
    de: 'German',
    it: 'Italian',
    pt: 'Portuguese',
    nl: 'Dutch',
    pl: 'Polish',
    ru: 'Russian',
    ja: 'Japanese',
    ko: 'Korean',
    zh: 'Chinese',
    ar: 'Arabic',
    hi: 'Hindi',
    tr: 'Turkish',
    sv: 'Swedish',
    da: 'Danish',
    fi: 'Finnish',
    no: 'Norwegian',
    uk: 'Ukrainian',
    cs: 'Czech',
    el: 'Greek',
    he: 'Hebrew',
    hu: 'Hungarian',
    ro: 'Romanian',
    th: 'Thai',
    vi: 'Vietnamese',
    id: 'Indonesian',
    ms: 'Malay',
    ca: 'Catalan',
  } as Record<string, string>,
} as const

const jobDetail = {
  errorLoading: 'Error loading job',
  notFound: 'Job not found',
  backToJobs: 'Back to Jobs',
  jobFailed: 'Job Failed',
  hideRawJson: 'Hide raw JSON',
  viewRawJson: 'View raw JSON',

  // Retention labels
  permanent: 'Permanent',
  transient: 'Transient',
  default: 'Default',
  pendingPurge: 'Pending purge',
  untilPurge: 'until purge',
  purged: 'Purged',
  noStorage: 'No storage',
  afterCompletion: 'after completion',

  // Pipeline
  pipeline: 'Pipeline',
  preparingPipeline: 'Preparing pipeline — selecting engines and building task graph...',
  currentStage: 'Current stage:',
  loadingPipeline: 'Loading pipeline...',

  // Tabs
  transcript: 'Transcript',
  audio: 'Audio',
  noTranscript: 'No transcript available',
  transcriptNotAvailable: 'Transcript not available for this job status',
  auditTrail: 'Audit Trail',
  noAuditEvents: 'No audit events recorded',

  // Error field labels
  error: 'Error',
  message: 'Message',
  engine: 'Engine',
  stage: 'Stage',
  suggestion: 'Suggestion',
} as const

const taskDetail = {
  errorLoading: 'Error loading task',
  backToJob: 'Back to Job',
  thisTask: 'This task',
  taskFailed: 'Task Failed',
  failedAfter: 'Failed after',
  retryAttempt: 'retry attempt',
  retryAttempts: 'retry attempts',

  // Metrics
  duration: 'Duration',
  started: 'Started',
  completed: 'Completed',
  retries: 'Retries',

  // Sections
  dependencies: 'Dependencies',
  input: 'Input',
  output: 'Output',
  hide: 'Hide',
  show: 'Show',
  rawJson: 'Raw JSON',
  noInputData: 'No input data available',
  noOutputFailed: 'No output - task failed before producing results',
  taskNotStarted: 'Task has not started yet',
  taskStillRunning: 'Task is still running...',

  // Transcript preview
  fullText: 'Full Text',
  segmentsFirst10: 'Segments (first 10)',
  andMoreSegments: (n: number) => `and ${n} more segments`,
  speakersDetected: 'Speakers Detected',
  speakerSegmentsDetected: 'speaker segments detected',
  language: 'Language:',
  segments: 'Segments:',
  alignmentFallback: 'Alignment fallback:',
  transcriptPreview: 'Transcript Preview',
} as const

const models = {
  title: 'Models',
  subtitle: 'Manage transcription models and download from HuggingFace',
  syncWithDisk: 'Sync with Disk',
  addFromHF: 'Add from HF',
  cardTitle: 'Model Registry',
  loadingModels: 'Loading models...',
  noModelsFound: 'No models found',
  noModelsHint: 'Add a model from HuggingFace or sync with disk',
} as const

const engines = {
  title: 'Engines',
  subtitle: 'Pipeline stages and processing capacity',
  batchEngines: 'Batch Engines',
  realtimeWorkers: 'Real-time Workers',
  pipelineStages: 'Pipeline Stages',
  issues: 'Issues',
  batchPipeline: 'Batch Pipeline',
  noEngines: 'no engines',
  enginesReady: 'engines ready',
  processing: 'processing',
  queued: 'queued',
  noEnginesForStage: 'No engines registered for this stage',
  noRealtimeWorkers: 'No real-time workers registered',
  noneReady: '(none ready)',
} as const

const engineDetail = {
  notFound: 'Engine Not Found',
  notFoundHint: 'The engine may not be running or may have been removed.',
  stageLabel: 'Stage:',
  versionLabel: 'Version:',
  processingMetric: 'Processing',
  activeTasks: 'active tasks',
  queueDepth: 'Queue Depth',
  waiting: 'waiting',
  maxDuration: 'Max Duration',
  maxConcurrency: 'Max Concurrency',
  capabilities: 'Capabilities',
  wordTimestamps: 'Word Timestamps',
  streaming: 'Streaming',
  maxAudioDuration: 'Max Audio Duration',
  supported: 'Supported',
  notSupported: 'Not supported',
  supportedLanguages: 'Supported Languages',
  multilingual: 'Multilingual — supports automatic language detection and all languages',
  noLanguageInfo: 'No language information available',
  availableModels: 'Available Models',
  noModelsForEngine: 'No models in registry for this engine',
  engineDetails: 'Engine Details',
} as const

const apiKeys = {
  title: 'API Keys',
  subtitle: 'Manage API keys for authentication',
  createKey: 'Create Key',
  cardTitle: 'API Keys',
  colPrefix: 'Prefix',
  colScopes: 'Scopes',
  colLastUsed: 'Last Used',
  revoke: 'Revoke',
  noKeysFound: 'No API keys found',
  noKeysHint: 'Try changing filters or create a key',

  // Sort
  lastUsedFirst: 'Last used first',
  leastRecentlyUsed: 'Least recently used',

  // Revoke dialog
  revokeKey: 'Revoke API Key',
  revokeConfirm: 'Are you sure you want to revoke this API key? This action cannot be undone.',
  revokeButton: 'Revoke Key',
  showingKeys: (shown: number, total: number) => `Showing ${shown} of ${total} keys`,
} as const

const createKeyDialog = {
  title: 'Create API Key',
  nameLabel: 'Name',
  namePlaceholder: 'e.g., Production API, CI Pipeline',
  scopesLabel: 'Scopes',
  rateLimitLabel: 'Rate Limit (optional)',
  rateLimitPlaceholder: 'Unlimited',
  rateLimitUnit: 'requests/minute',
  creating: 'Creating...',

  // Scopes
  scopes: {
    read: { label: 'Read Jobs', description: 'View job status and results' },
    create: { label: 'Create Jobs', description: 'Submit transcription jobs' },
    realtime: { label: 'Real-time', description: 'Connect to WebSocket streams' },
    webhooks: { label: 'Webhooks', description: 'Manage webhook configurations' },
    admin: { label: 'Admin Access', description: 'Full console access (grants all permissions)' },
  },
  adminWarningTitle: 'Admin scope selected',
  adminWarningText:
    'This key will have full access to all API operations including key management.',
} as const

const keyCreatedModal = {
  title: 'API Key Created',
  saveWarningTitle: 'Save this key now',
  saveWarningText:
    'This is the only time you will see the full API key. Store it securely!',
  apiKeyLabel: 'API Key',
  usageSection: 'Usage',
} as const

const settings = {
  title: 'Settings',
  subtitle: 'Manage system configuration and operational parameters',
  readOnlyNotice: 'System settings are read-only and controlled by environment variables.',
  modified: 'Modified',
  unsavedChange: 'unsaved change',
  unsavedChanges: 'unsaved changes',
  savedSuccessfully: 'Settings saved successfully.',
  resetToDefaults: 'Reset to defaults',
  resetting: 'Resetting...',
  resetConfirm: 'This will revert all settings in this section to their default values.',
  validationError: 'Validation error',
} as const

const realtimeLive = {
  title: 'Live Transcription',
  subtitle: 'Start a real-time transcription session using your microphone',
  settingsButton: 'Settings',
  languageLabel: 'Language',
  modelLabel: 'Model',
  anyAvailable: 'Any available',
  vocabularyLabel: 'Vocabulary (comma-separated terms to boost recognition)',
  vocabularyPlaceholder: 'e.g., Kubernetes, CrewAI, Bizzon',
  noModelsWarning:
    'No downloaded models for available runtimes. Visit Models page to download.',
  unavailable:
    'Real-time transcription is currently unavailable. No workers are ready.',
  checkEngineHealth: 'Check engine health',
  atCapacity: 'All worker capacity is currently in use',
  startSession: 'Start Session',
  connecting: 'Connecting...',
  stop: 'Stop',
  finishing: 'Finishing...',
  recording: 'Recording',
  transcriptTitle: 'Transcript',
  words: 'words',
  segments: 'segments',
  sessionCompleted: 'Session completed',
} as const

const realtimeSessions = {
  title: 'Real-time',
  subtitle: 'Real-time transcription workers, capacity, and session history',
  newSession: 'New Session',
  statusTitle: 'Status',
  activeSessions: 'Active Sessions',
  available: 'available',
  workersTitle: 'Workers',
  ready: 'ready',
  capacityOverview: 'Capacity Overview',
  used: 'Used',
  sessions: 'sessions',

  // Health states
  statusNotAvailable: 'Status not available',
  statusNotLoaded: 'Real-time status data has not loaded yet.',
  statusNotLoadedHint: 'Refresh status and check engine health if this persists.',
  noWorkersRunning: 'No real-time workers running',
  noWorkersDetail: 'Real-time is unavailable because no workers are registered.',
  startWorkerHint: 'Start at least one real-time worker...',
  workersUnhealthy: 'Workers unhealthy',
  workersUnhealthyDetail: (ready: number, total: number) =>
    `Real-time is unavailable: ${ready}/${total} workers are ready.`,
  atCapacityTitle: 'At capacity',
  atCapacityDetail: 'All available capacity is currently in use.',
  atCapacityHint: 'Wait for active sessions to finish or scale workers...',
  healthyTitle: 'Real-time healthy',
  healthyDetail: 'Workers are ready and can accept new sessions.',
  healthyHint: 'No action needed unless you expect higher throughput.',
  startWorker: 'Start worker (self-hosted)',
  whyThisState: 'Why this state?',
  hideWhyThisState: 'Hide why this state',
  selfHostedQuickStart: 'Self-hosted quick start:',
  scaleInstructions:
    'On AWS/ECS/Kubernetes, scale your realtime worker service/deployment instead.',

  // Sessions table
  sessionsTitle: 'Sessions',
  noSessionsFound: 'No sessions found',
  colId: 'ID',
  colSegments: 'Segments',

  // Delete dialog
  deleteSession: 'Delete Session',
  deleteConfirm:
    'This will permanently delete the session and all its stored data (audio, transcripts). This action cannot be undone.',
} as const

const auditLog = {
  title: 'Audit Log',
  subtitle: 'View data access and lifecycle events',
  eventsTitle: 'Events',
  filters: 'Filters',
  activeFilters: 'Active',

  // Filter labels
  resourceType: 'Resource Type',
  action: 'Action',
  actorId: 'Actor ID',
  since: 'Since',
  until: 'Until',
  sort: 'Sort',
  rowsPerPage: 'Rows per page',
  allResources: 'All Resources',
  actionPlaceholder: 'e.g., job.created',
  actorPlaceholder: 'e.g., dk_abc1234',

  // Resource types
  resourceTypes: {
    job: 'Job',
    transcript: 'Transcript',
    audio: 'Audio',
    session: 'Session',
    apiKey: 'API Key',
    retentionPolicy: 'Retention Policy',
  },

  // Column headers
  colTimestamp: 'Timestamp',
  colAction: 'Action',
  colResource: 'Resource',
  colActor: 'Actor',
  colIpAddress: 'IP Address',

  noEventsFound: 'No audit events found',
  noEventsHint: 'Try adjusting your filters',

  // Action categories
  actionLabels: {
    created: 'Created',
    completed: 'Completed',
    accessed: 'Accessed',
    exported: 'Exported',
    deleted: 'Deleted',
    purged: 'Purged',
    failed: 'Failed',
    started: 'Started',
    ended: 'Ended',
    revoked: 'Revoked',
    cancelled: 'Cancelled',
  },
} as const

const webhooks = {
  title: 'Webhooks',
  subtitle: 'Manage webhook endpoints for event notifications',
  createWebhook: 'Create Webhook',
  cardTitle: 'Webhook Endpoints',
  colUrl: 'URL',
  colEvents: 'Events',
  autoDisabled: 'Auto-disabled',
  noWebhooksFound: 'No webhook endpoints found',
  noWebhooksHint: 'Try changing filters or create a new endpoint',
  consecutiveFailure: 'consecutive failure',
  consecutiveFailures: 'consecutive failures',
  deactivate: 'Deactivate',
  activate: 'Activate',
  rotateSecret: 'Rotate secret',

  // Delete dialog
  deleteWebhook: 'Delete Webhook',
  deleteConfirm:
    'Are you sure you want to delete this webhook? This will also delete all delivery history. This action cannot be undone.',
} as const

const createWebhookDialog = {
  title: 'Create Webhook Endpoint',
  urlLabel: 'URL',
  urlPlaceholder: 'https://your-server.com/webhooks/dalston',
  urlHint: 'Must be HTTPS in production. Webhook payloads will be POSTed here.',
  descriptionLabel: 'Description (optional)',
  descriptionPlaceholder: 'e.g., Production notification handler',
  eventsLabel: 'Events',
  creating: 'Creating...',

  events: {
    completed: {
      label: 'Transcription Completed',
      description: 'Triggered when a job finishes successfully',
    },
    failed: {
      label: 'Transcription Failed',
      description: 'Triggered when a job fails permanently',
    },
    cancelled: {
      label: 'Transcription Cancelled',
      description: 'Triggered when a job is cancelled by the user',
    },
    all: {
      label: 'All Events',
      description: 'Subscribe to all event types (current and future)',
    },
  },
} as const

const modelTable = {
  colRuntime: 'Runtime',
  colSize: 'Size',
  colCapabilities: 'Capabilities',
  removeDownloaded: 'Remove downloaded files',
  downloading: 'Downloading...',
  downloadModel: 'Download model',
  deleteFromRegistry: 'Delete from registry',
  deleteTitle: 'Delete from Registry',
  deleteConfirm:
    'This will remove the model from the registry and delete any downloaded files. You can re-add it from HuggingFace later if needed.',

  // Status
  statusReady: 'Ready',
  statusAvailable: 'Available',
  statusDownloading: 'Downloading',
  statusFailed: 'Failed',

  // Capabilities
  capWord: 'word',
  capSegment: 'segment',
  capStream: 'stream',
  capGpuOnly: 'GPU Only',
  wordTimestamps: 'Word-level timestamps',
  segmentTimestamps: 'Segment-level timestamps only',
  punctuation: 'Punctuation',
  noPunctuation: 'No punctuation',
  streamingSupport: 'Streaming support',
  batchOnly: 'Batch only',

  // Hardware
  hardwareTitle: 'Hardware Requirements',
  cpuCompatible: 'CPU compatible',
  gpuRequired: 'GPU required',
  viewOnHuggingFace: 'View on HuggingFace',
  languagesAndInfo: 'Languages & Info',
  multilingual: 'Multilingual',
  nMore: (n: number) => `${n} more`,
} as const

const modelFilters = {
  searchPlaceholder: 'Search models...',
  allStages: 'All stages',
  allRuntimes: 'All runtimes',
  allStatuses: 'All statuses',
  stages: { transcribe: 'Transcribe', align: 'Align', diarize: 'Diarize' },
  runtimes: {
    fasterWhisper: 'Faster Whisper',
    nemo: 'NeMo',
    whisperx: 'WhisperX',
    huggingfaceAsr: 'HuggingFace ASR',
    pyannote: 'Pyannote',
  },
  statuses: {
    ready: 'Ready',
    downloading: 'Downloading',
    notDownloaded: 'Not Downloaded',
    failed: 'Failed',
  },
} as const

const capabilities = {
  title: 'System Capabilities',
  wordTimestamps: 'Word Timestamps',
  speakerDiarization: 'Speaker Diarization',
  piiDetection: 'PII Detection',
  streaming: 'Streaming',
} as const

// ---------------------------------------------------------------------------
// Status filter options (shared across multiple pages)
// ---------------------------------------------------------------------------

const statusFilters = {
  job: [
    { label: 'All', value: '' as const },
    { label: 'Pending', value: 'pending' as const },
    { label: 'Running', value: 'running' as const },
    { label: 'Completed', value: 'completed' as const },
    { label: 'Failed', value: 'failed' as const },
    { label: 'Cancelled', value: 'cancelled' as const },
  ],
  session: [
    { label: 'All', value: '' as const },
    { label: 'Active', value: 'active' as const },
    { label: 'Completed', value: 'completed' as const },
    { label: 'Error', value: 'error' as const },
    { label: 'Interrupted', value: 'interrupted' as const },
  ],
  webhook: [
    { label: 'All', value: '' as const },
    { label: 'Active', value: 'active' as const },
    { label: 'Inactive', value: 'inactive' as const },
  ],
  apiKey: [
    { label: 'Active', value: 'active' as const },
    { label: 'All', value: '' as const },
    { label: 'Revoked', value: 'revoked' as const },
  ],
} as const

// ---------------------------------------------------------------------------
// Engine status labels (used in Engines page and EngineDetail)
// ---------------------------------------------------------------------------

const engineStatus = {
  idle: 'Idle',
  processing: 'Processing',
  loadingModel: 'Loading model',
  downloadingModel: 'Downloading model',
  stale: 'Stale',
  error: 'Error',
  offline: 'Offline',
} as const

// ---------------------------------------------------------------------------
// Export
// ---------------------------------------------------------------------------

export const S = {
  common,
  errors,
  login,
  dashboard,
  batchJobs,
  newJob,
  jobDetail,
  taskDetail,
  models,
  engines,
  engineDetail,
  apiKeys,
  createKeyDialog,
  keyCreatedModal,
  settings,
  realtimeLive,
  realtimeSessions,
  auditLog,
  webhooks,
  createWebhookDialog,
  modelTable,
  modelFilters,
  capabilities,
  statusFilters,
  engineStatus,
} as const
