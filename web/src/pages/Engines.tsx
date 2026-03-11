import { useMemo } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  ChevronDown,
  ChevronRight,
  Server,
  Radio,
  AlertCircle,
  Layers,
  Box,
} from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { useEngines } from '@/hooks/useEngines'
import { useModelRegistry } from '@/hooks/useModelRegistry'
import type { BatchEngine, EngineStatus, WorkerStatus, ModelRegistryEntry, ModelStatus } from '@/api/types'
import { cn } from '@/lib/utils'
import { S } from '@/lib/strings'

// Pipeline stages in their natural processing order
const PIPELINE_STAGES = [
  { id: 'prepare', label: 'Prepare', description: 'Audio preprocessing' },
  { id: 'transcribe', label: 'Transcribe', description: 'Speech-to-text' },
  { id: 'align', label: 'Align', description: 'Word-level timestamps' },
  { id: 'diarize', label: 'Diarize', description: 'Speaker identification' },
  { id: 'pii_detect', label: 'PII Detect', description: 'Sensitive data detection' },
  { id: 'audio_redact', label: 'Audio Redact', description: 'PII audio masking' },
  { id: 'merge', label: 'Merge', description: 'Final assembly' },
] as const

type StageId = (typeof PIPELINE_STAGES)[number]['id']

interface StageStatus {
  stage: (typeof PIPELINE_STAGES)[number]
  engines: BatchEngine[]
  healthyCount: number
  unhealthyCount: number
  totalQueueDepth: number
  totalProcessing: number
}

type DotStatus = 'healthy' | 'unhealthy' | 'warning' | 'empty'

function StatusDot({ status }: { status: DotStatus }) {
  const colors: Record<DotStatus, string> = {
    healthy: 'bg-green-500',
    unhealthy: 'bg-red-500',
    warning: 'bg-yellow-500',
    empty: 'bg-zinc-500',
  }
  return <span className={cn('inline-block w-2 h-2 rounded-full shrink-0', colors[status])} />
}

/** Map engine status to a dot status for the UI. */
function engineStatusToDot(status: EngineStatus): DotStatus {
  switch (status) {
    case 'idle':
    case 'processing':
      return 'healthy'
    case 'loading':
    case 'downloading':
    case 'stale':
      return 'warning'
    case 'error':
    case 'offline':
      return 'unhealthy'
  }
}

/** Human-readable label for an engine status. */
function engineStatusLabel(status: EngineStatus): string {
  switch (status) {
    case 'idle':
      return 'Idle'
    case 'processing':
      return 'Processing'
    case 'loading':
      return 'Loading model'
    case 'downloading':
      return 'Downloading model'
    case 'stale':
      return 'Stale'
    case 'error':
      return 'Error'
    case 'offline':
      return 'Offline'
  }
}

function isEngineHealthy(engine: BatchEngine): boolean {
  return engineStatusToDot(engine.status) !== 'unhealthy'
}

function getStageAggregateStatus(engines: BatchEngine[]): DotStatus {
  if (engines.length === 0) return 'empty'

  let hasWarning = false
  let hasUnhealthy = false
  let allUnhealthy = true

  for (const engine of engines) {
    const dot = engineStatusToDot(engine.status)
    if (dot === 'unhealthy') {
      hasUnhealthy = true
    } else if (dot === 'warning') {
      hasWarning = true
      allUnhealthy = false
    } else {
      allUnhealthy = false
    }
  }

  if (allUnhealthy) return 'unhealthy'
  if (hasUnhealthy || hasWarning) return 'warning'
  return 'healthy'
}

function StageHeader({
  stageStatus,
  isExpanded,
  onToggle,
}: {
  stageStatus: StageStatus
  isExpanded: boolean
  onToggle: () => void
}) {
  const { stage, engines, healthyCount, totalQueueDepth, totalProcessing } = stageStatus
  const aggregateStatus = getStageAggregateStatus(engines)

  const summaryParts: string[] = []
  if (engines.length === 0) {
    summaryParts.push(S.engines.noEngines)
  } else if (healthyCount === engines.length) {
    // All engines ready - just show count
    summaryParts.push(`${engines.length} engine${engines.length !== 1 ? 's' : ''}`)
  } else {
    // Some engines offline - show fraction
    summaryParts.push(`${healthyCount}/${engines.length} ${S.engines.enginesReady}`)
  }
  if (totalQueueDepth > 0 || totalProcessing > 0) {
    const activityParts: string[] = []
    if (totalProcessing > 0) activityParts.push(`${totalProcessing} ${S.engines.processing}`)
    if (totalQueueDepth > 0) activityParts.push(`${totalQueueDepth} ${S.engines.queued}`)
    summaryParts.push(activityParts.join(', '))
  }

  return (
    <button
      onClick={onToggle}
      className={cn(
        'w-full flex items-center justify-between p-4 text-left rounded-lg transition-colors',
        'hover:bg-accent/50',
        isExpanded && 'bg-accent/30'
      )}
    >
      <div className="flex items-center gap-3">
        {isExpanded ? (
          <ChevronDown className="h-4 w-4 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-4 w-4 text-muted-foreground" />
        )}
        <StatusDot status={aggregateStatus} />
        <div>
          <span className="font-medium">{stage.label}</span>
          <span className="text-muted-foreground ml-2 text-sm hidden sm:inline">
            {stage.description}
          </span>
        </div>
      </div>
      <div className="flex items-center gap-3">
        <span className="text-sm text-muted-foreground">{summaryParts.join(' · ')}</span>
      </div>
    </button>
  )
}

// Model status styling (only ready vs not ready matters on this page)
const modelStatusColors: Record<ModelStatus, string> = {
  ready: 'bg-green-500',
  downloading: 'bg-zinc-400',
  not_downloaded: 'bg-zinc-400',
  failed: 'bg-zinc-400',
}

const modelStatusLabels: Record<ModelStatus, string> = {
  ready: 'Ready',
  downloading: 'Not Downloaded',
  not_downloaded: 'Not Downloaded',
  failed: 'Not Downloaded',
}

// Stage-specific info to show in engine cards
function getStageSpecificInfo(stage: string, models: ModelRegistryEntry[]): React.ReactNode {
  // Only show models for transcribe stage (diarize doesn't use model registry)
  if (stage !== 'transcribe') return null

  // No models in registry for this engine_id
  if (models.length === 0) {
    return (
      <div className="mt-3 flex items-center gap-2">
        <Box className="h-3 w-3 text-muted-foreground shrink-0" />
        <span className="text-xs text-muted-foreground italic">No models in registry</span>
      </div>
    )
  }

  // Sort: ready first, then downloading, then not_downloaded, then failed
  const statusOrder: Record<ModelStatus, number> = { ready: 0, downloading: 1, not_downloaded: 2, failed: 3 }
  const sortedModels = [...models].sort((a, b) => statusOrder[a.status] - statusOrder[b.status])
  const readyCount = models.filter((m) => m.status === 'ready').length
  const maxToShow = 3

  return (
    <div className="mt-3 flex items-center gap-2">
      <Box className="h-3 w-3 text-muted-foreground shrink-0" />
      <div className="flex flex-wrap gap-1">
        {sortedModels.slice(0, maxToShow).map((model) => (
          <Badge
            key={model.id}
            variant={model.status === 'ready' ? 'secondary' : 'outline'}
            className={cn(
              'text-xs',
              model.status === 'not_downloaded' && 'opacity-60'
            )}
          >
            <span
              className={cn('w-1.5 h-1.5 rounded-full mr-1', modelStatusColors[model.status])}
              title={modelStatusLabels[model.status]}
            />
            {model.name || model.id}
          </Badge>
        ))}
        {models.length > maxToShow && (
          <Badge variant="outline" className="text-xs">
            +{models.length - maxToShow} more
          </Badge>
        )}
        {readyCount === 0 && (
          <span className="text-xs text-muted-foreground italic ml-1">
            {S.engines.noneReady}
          </span>
        )}
      </div>
    </div>
  )
}

function EngineCard({ engine, models }: { engine: BatchEngine; models: ModelRegistryEntry[] }) {
  const dot = engineStatusToDot(engine.status)
  const hasActivity = engine.processing > 0 || engine.queue_depth > 0

  return (
    <Link
      to={`/engines/${encodeURIComponent(engine.engine_id)}`}
      className={cn(
        'block p-4 rounded-lg border transition-all',
        'hover:border-primary/50 hover:bg-accent/30',
        'focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2',
        dot === 'unhealthy' && 'border-red-500/30 bg-red-500/5'
      )}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-3 min-w-0">
          <StatusDot status={dot} />
          <div className="flex items-center gap-2 min-w-0">
            <span className="font-medium truncate">{engine.engine_id}</span>
            <span className="text-sm text-muted-foreground">·</span>
            <span className="text-sm text-muted-foreground shrink-0">{engineStatusLabel(engine.status)}</span>
          </div>
        </div>
        {hasActivity && (
          <div className="text-right shrink-0">
            {engine.processing > 0 && (
              <div className="text-sm">
                <span className="font-medium">{engine.processing}</span>
                <span className="text-muted-foreground ml-1">{S.engines.processing}</span>
              </div>
            )}
            {engine.queue_depth > 0 && (
              <div className="text-sm">
                <span className="font-medium">{engine.queue_depth}</span>
                <span className="text-muted-foreground ml-1">{S.engines.queued}</span>
              </div>
            )}
          </div>
        )}
      </div>
      {getStageSpecificInfo(engine.stage, models)}
    </Link>
  )
}

function StageAccordion({
  stageStatus,
  isExpanded,
  onToggle,
  modelsByRuntime,
}: {
  stageStatus: StageStatus
  isExpanded: boolean
  onToggle: () => void
  modelsByRuntime: Map<string, ModelRegistryEntry[]>
}) {
  return (
    <div className="border rounded-lg overflow-hidden">
      <StageHeader stageStatus={stageStatus} isExpanded={isExpanded} onToggle={onToggle} />
      {isExpanded && stageStatus.engines.length > 0 && (
        <div className="p-4 space-y-2">
          {stageStatus.engines.map((engine) => (
            <EngineCard
              key={engine.engine_id}
              engine={engine}
              models={modelsByRuntime.get(engine.engine_id) ?? []}
            />
          ))}
        </div>
      )}
      {isExpanded && stageStatus.engines.length === 0 && (
        <div className="p-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground p-4 rounded-lg bg-muted/30 border border-dashed">
            <AlertCircle className="h-4 w-4" />
            {S.engines.noEnginesForStage}
          </div>
        </div>
      )}
    </div>
  )
}

function RealtimeWorkerCard({ worker, models }: { worker: WorkerStatus; models: ModelRegistryEntry[] }) {
  const isReady = worker.status === 'ready'
  const utilization = worker.capacity > 0 ? (worker.active_sessions / worker.capacity) * 100 : 0
  const loadedModelSet = new Set(worker.models)

  // Sort: loaded first, then by status (ready > downloading > not_downloaded > failed)
  const statusOrder: Record<ModelStatus, number> = { ready: 0, downloading: 1, not_downloaded: 2, failed: 3 }
  const sortedModels = [...models].sort((a, b) => {
    const aLoaded = loadedModelSet.has(a.id) || loadedModelSet.has(a.name || '')
    const bLoaded = loadedModelSet.has(b.id) || loadedModelSet.has(b.name || '')
    if (aLoaded !== bLoaded) return aLoaded ? -1 : 1
    return statusOrder[a.status] - statusOrder[b.status]
  })
  const maxToShow = 4

  return (
    <Link
      to={`/realtime/workers/${encodeURIComponent(worker.instance)}`}
      className={cn(
        'block p-4 rounded-lg border transition-all',
        'hover:border-primary/50 hover:bg-accent/30',
        'focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2',
        isReady ? 'border-border' : 'border-red-500/30 bg-red-500/5'
      )}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-3 min-w-0">
          <StatusDot status={isReady ? 'healthy' : 'unhealthy'} />
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-medium truncate">{worker.instance}</span>
              {worker.engine_id && (
                <Badge variant="outline" className="text-xs shrink-0">
                  {worker.engine_id}
                </Badge>
              )}
            </div>
            <div className="text-xs text-muted-foreground truncate">{worker.endpoint}</div>
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className="text-sm">
            <span className="font-medium">{worker.active_sessions}</span>
            <span className="text-muted-foreground">/{worker.capacity}</span>
            <span className="text-muted-foreground ml-1">sessions</span>
          </div>
          {utilization > 0 && (
            <div className="mt-1 h-1.5 w-20 bg-muted rounded-full overflow-hidden">
              <div
                className={cn(
                  'h-full rounded-full transition-all',
                  utilization > 80 ? 'bg-yellow-500' : 'bg-green-500'
                )}
                style={{ width: `${Math.min(utilization, 100)}%` }}
              />
            </div>
          )}
        </div>
      </div>
      {models.length > 0 && (
        <div className="mt-3 flex items-center gap-2">
          <Box className="h-3 w-3 text-muted-foreground shrink-0" />
          <div className="flex flex-wrap gap-1">
            {sortedModels.slice(0, maxToShow).map((model) => {
              const isLoaded = loadedModelSet.has(model.id) || loadedModelSet.has(model.name || '')
              return (
                <Badge
                  key={model.id}
                  variant={isLoaded ? 'secondary' : 'outline'}
                  className={cn('text-xs', !isLoaded && model.status === 'not_downloaded' && 'opacity-60')}
                >
                  <span
                    className={cn('w-1.5 h-1.5 rounded-full mr-1', isLoaded ? 'bg-blue-500' : modelStatusColors[model.status])}
                    title={isLoaded ? 'Loaded' : modelStatusLabels[model.status]}
                  />
                  {model.name || model.id}
                </Badge>
              )
            })}
            {models.length > maxToShow && (
              <Badge variant="outline" className="text-xs">
                +{models.length - maxToShow} more
              </Badge>
            )}
          </div>
        </div>
      )}
    </Link>
  )
}

export function Engines() {
  const { data, isLoading, error } = useEngines()
  const { data: registryData } = useModelRegistry()
  const [searchParams, setSearchParams] = useSearchParams()

  // Parse expanded stages from URL (e.g., ?expanded=transcribe,diarize)
  const expandedStages = useMemo(() => {
    const param = searchParams.get('expanded')
    if (!param) return new Set<StageId>()
    const ids = param.split(',').filter((id): id is StageId =>
      PIPELINE_STAGES.some((s) => s.id === id)
    )
    return new Set(ids)
  }, [searchParams])

  const batchEngines = useMemo(() => data?.batch_engines ?? [], [data?.batch_engines])
  const realtimeWorkers = data?.realtime_engines ?? []

  // Group models by engine_id (engine)
  const modelsByRuntime = useMemo(() => {
    const map = new Map<string, ModelRegistryEntry[]>()
    for (const model of registryData?.data ?? []) {
      const existing = map.get(model.engine_id) ?? []
      existing.push(model)
      map.set(model.engine_id, existing)
    }
    return map
  }, [registryData?.data])

  // Group engines by stage and compute stats
  const stageStatuses = useMemo((): StageStatus[] => {
    const enginesByStage = new Map<string, BatchEngine[]>()
    for (const engine of batchEngines) {
      const existing = enginesByStage.get(engine.stage) ?? []
      existing.push(engine)
      enginesByStage.set(engine.stage, existing)
    }

    return PIPELINE_STAGES.map((stage) => {
      const engines = enginesByStage.get(stage.id) ?? []
      const healthyCount = engines.filter(isEngineHealthy).length
      const unhealthyCount = engines.length - healthyCount
      const totalQueueDepth = engines.reduce((sum, e) => sum + e.queue_depth, 0)
      const totalProcessing = engines.reduce((sum, e) => sum + e.processing, 0)

      return {
        stage,
        engines,
        healthyCount,
        unhealthyCount,
        totalQueueDepth,
        totalProcessing,
      }
    })
  }, [batchEngines])

  // Toggle stage expansion and persist to URL
  const toggleStage = (stageId: StageId) => {
    const next = new Set(expandedStages)
    if (next.has(stageId)) {
      next.delete(stageId)
    } else {
      next.add(stageId)
    }
    // Update URL params
    setSearchParams((prev) => {
      if (next.size === 0) {
        prev.delete('expanded')
      } else {
        prev.set('expanded', Array.from(next).join(','))
      }
      return prev
    }, { replace: true })
  }

  // Summary stats
  const totalEngines = batchEngines.length
  const healthyEngines = batchEngines.filter(isEngineHealthy).length
  const totalWorkers = realtimeWorkers.length
  const readyWorkers = realtimeWorkers.filter((w) => w.status === 'ready').length

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold">{S.engines.title}</h1>
        <p className="text-muted-foreground">
          {S.engines.subtitle}
        </p>
      </div>

      {/* Error state */}
      {error && (
        <div className="p-4 bg-destructive/10 text-destructive rounded-md flex items-center gap-2">
          <AlertCircle className="h-4 w-4" />
          Failed to load engine status
        </div>
      )}

      {/* Summary cards */}
      <div className="grid gap-4 grid-cols-2 sm:grid-cols-4">
        <Card>
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <Server className="h-5 w-5 text-muted-foreground" />
              <div>
                <p className="text-xs text-muted-foreground">{S.engines.batchEngines}</p>
                <p className="text-lg font-semibold">
                  {healthyEngines}
                  <span className="text-muted-foreground text-sm font-normal">/{totalEngines}</span>
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <Radio className="h-5 w-5 text-muted-foreground" />
              <div>
                <p className="text-xs text-muted-foreground">{S.engines.realtimeWorkers}</p>
                <p className="text-lg font-semibold">
                  {readyWorkers}
                  <span className="text-muted-foreground text-sm font-normal">/{totalWorkers}</span>
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <Layers className="h-5 w-5 text-muted-foreground" />
              <div>
                <p className="text-xs text-muted-foreground">{S.engines.pipelineStages}</p>
                <p className="text-lg font-semibold">
                  {stageStatuses.filter((s) => s.engines.length > 0).length}
                  <span className="text-muted-foreground text-sm font-normal">/{PIPELINE_STAGES.length}</span>
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <AlertCircle className="h-5 w-5 text-muted-foreground" />
              <div>
                <p className="text-xs text-muted-foreground">{S.engines.issues}</p>
                <p className="text-lg font-semibold">
                  {totalEngines - healthyEngines + (totalWorkers - readyWorkers)}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Batch Pipeline */}
      <Card>
        <CardHeader className="flex flex-row items-center gap-2">
          <Server className="h-5 w-5 text-muted-foreground" />
          <CardTitle>{S.engines.batchPipeline}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {isLoading ? (
            <div className="space-y-2">
              {[1, 2, 3, 4].map((i) => (
                <div key={i} className="h-14 bg-muted animate-pulse rounded-lg" />
              ))}
            </div>
          ) : (
            stageStatuses.map((stageStatus) => (
              <StageAccordion
                key={stageStatus.stage.id}
                stageStatus={stageStatus}
                isExpanded={expandedStages.has(stageStatus.stage.id)}
                onToggle={() => toggleStage(stageStatus.stage.id)}
                modelsByRuntime={modelsByRuntime}
              />
            ))
          )}
        </CardContent>
      </Card>

      {/* Real-time Workers */}
      <Card>
        <CardHeader className="flex flex-row items-center gap-2">
          <Radio className="h-5 w-5 text-muted-foreground" />
          <CardTitle>{S.engines.realtimeWorkers}</CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-2">
              {[1, 2].map((i) => (
                <div key={i} className="h-16 bg-muted animate-pulse rounded-lg" />
              ))}
            </div>
          ) : realtimeWorkers.length === 0 ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground p-4 rounded-lg bg-muted/30 border border-dashed">
              <AlertCircle className="h-4 w-4" />
              {S.engines.noRealtimeWorkers}
            </div>
          ) : (
            <div className="space-y-2">
              {realtimeWorkers.map((worker) => (
                <RealtimeWorkerCard
                  key={worker.instance}
                  worker={worker}
                  models={modelsByRuntime.get(worker.engine_id ?? '') ?? []}
                />
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
