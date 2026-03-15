import { useMemo } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  ChevronDown,
  ChevronRight,
  Server,
  AlertCircle,
  Layers,
  Box,
  Users,
} from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { useEngines } from '@/hooks/useEngines'
import { useModelRegistry } from '@/hooks/useModelRegistry'
import type { EngineStatus, ModelRegistryEntry, ModelStatus, WorkerStatus } from '@/api/types'
import { cn } from '@/lib/utils'
import { S } from '@/lib/strings'

// Sort-order hint for known stages — unknown stages appear at the end
const STAGE_ORDER: Record<string, number> = {
  prepare: 0,
  transcribe: 1,
  align: 2,
  diarize: 3,
  pii_detect: 4,
  audio_redact: 5,
  merge: 6,
}

const STAGE_LABELS: Record<string, { label: string; description: string }> = {
  prepare: { label: 'Prepare', description: 'Audio preprocessing' },
  transcribe: { label: 'Transcribe', description: 'Speech-to-text' },
  align: { label: 'Align', description: 'Word-level timestamps' },
  diarize: { label: 'Diarize', description: 'Speaker identification' },
  pii_detect: { label: 'PII Detect', description: 'Sensitive data detection' },
  audio_redact: { label: 'Audio Redact', description: 'PII audio masking' },
  merge: { label: 'Merge', description: 'Final assembly' },
}

/** Unified engine combining batch and realtime data. */
interface UnifiedEngine {
  engine_id: string
  stage: string
  status: EngineStatus
  queue_depth: number
  processing: number
  // Realtime metrics (from matched worker)
  active_sessions: number
  capacity: number
  worker_status: WorkerStatus['status'] | null
}

interface StageStatus {
  id: string
  label: string
  description: string
  engines: UnifiedEngine[]
  healthyCount: number
  totalQueueDepth: number
  totalProcessing: number
  totalSessions: number
  totalCapacity: number
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

function isEngineHealthy(engine: UnifiedEngine): boolean {
  return engineStatusToDot(engine.status) !== 'unhealthy'
}

function getStageAggregateStatus(engines: UnifiedEngine[]): DotStatus {
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
  const { engines, healthyCount, totalQueueDepth, totalProcessing, totalSessions } = stageStatus
  const aggregateStatus = getStageAggregateStatus(engines)

  const summaryParts: string[] = []
  if (engines.length === 0) {
    summaryParts.push(S.engines.noEngines)
  } else if (healthyCount === engines.length) {
    summaryParts.push(`${engines.length} engine${engines.length !== 1 ? 's' : ''}`)
  } else {
    summaryParts.push(`${healthyCount}/${engines.length} ${S.engines.enginesReady}`)
  }
  if (totalQueueDepth > 0 || totalProcessing > 0 || totalSessions > 0) {
    const activityParts: string[] = []
    if (totalProcessing > 0) activityParts.push(`${totalProcessing} ${S.engines.processing}`)
    if (totalQueueDepth > 0) activityParts.push(`${totalQueueDepth} ${S.engines.queued}`)
    if (totalSessions > 0) activityParts.push(`${totalSessions} ${S.engines.sessions}`)
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
          <span className="font-medium">{stageStatus.label}</span>
          <span className="text-muted-foreground ml-2 text-sm hidden sm:inline">
            {stageStatus.description}
          </span>
        </div>
      </div>
      <div className="flex items-center gap-3">
        <span className="text-sm text-muted-foreground">{summaryParts.join(' · ')}</span>
      </div>
    </button>
  )
}

// Model status styling
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

function getStageSpecificInfo(models: ModelRegistryEntry[]): React.ReactNode {
  if (models.length === 0) {
    return (
      <div className="mt-3 flex items-center gap-2">
        <Box className="h-3 w-3 text-muted-foreground shrink-0" />
        <span className="text-xs text-muted-foreground italic">No models in registry</span>
      </div>
    )
  }

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

function EngineCard({ engine, models }: { engine: UnifiedEngine; models: ModelRegistryEntry[] }) {
  const dot = engineStatusToDot(engine.status)
  const hasActivity = engine.processing > 0 || engine.queue_depth > 0
  const hasSessions = engine.capacity > 0
  const utilization = engine.capacity > 0 ? (engine.active_sessions / engine.capacity) * 100 : 0

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
        <div className="text-right shrink-0 flex items-center gap-4">
          {hasActivity && (
            <div>
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
          {hasSessions && (
            <div className="flex items-center gap-2">
              <Users className="h-3.5 w-3.5 text-muted-foreground" />
              <div className="text-sm">
                <span className="font-medium">{engine.active_sessions}</span>
                <span className="text-muted-foreground">/{engine.capacity}</span>
              </div>
              {utilization > 0 && (
                <div className="h-1.5 w-16 bg-muted rounded-full overflow-hidden">
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
          )}
        </div>
      </div>
      {getStageSpecificInfo(models)}
    </Link>
  )
}

function StageAccordion({
  stageStatus,
  isExpanded,
  onToggle,
  modelsByEngine,
}: {
  stageStatus: StageStatus
  isExpanded: boolean
  onToggle: () => void
  modelsByEngine: Map<string, ModelRegistryEntry[]>
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
              models={modelsByEngine.get(engine.engine_id) ?? []}
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

export function Engines() {
  const { data, isLoading, error } = useEngines()
  const { data: registryData } = useModelRegistry()
  const [searchParams, setSearchParams] = useSearchParams()

  // Merge batch engines with realtime worker data into unified engines
  const unifiedEngines = useMemo(() => {
    if (!data) return []

    // Index realtime workers by engine_id
    const workersByEngineId = new Map<string, WorkerStatus[]>()
    for (const worker of data.realtime_engines ?? []) {
      if (worker.engine_id) {
        const existing = workersByEngineId.get(worker.engine_id) ?? []
        existing.push(worker)
        workersByEngineId.set(worker.engine_id, existing)
      }
    }

    return (data.batch_engines ?? []).map((batch): UnifiedEngine => {
      const workers = workersByEngineId.get(batch.engine_id) ?? []
      const totalCapacity = workers.reduce((sum, w) => sum + w.capacity, 0)
      const totalSessions = workers.reduce((sum, w) => sum + w.active_sessions, 0)
      // Best worker status
      const workerStatus = workers.length > 0
        ? (workers.some((w) => w.status === 'ready') ? 'ready' : workers[0].status)
        : null

      return {
        engine_id: batch.engine_id,
        stage: batch.stage,
        status: batch.status,
        queue_depth: batch.queue_depth,
        processing: batch.processing,
        active_sessions: totalSessions,
        capacity: totalCapacity,
        worker_status: workerStatus,
      }
    })
  }, [data])

  // Derive stages dynamically from engine data
  const stageIds = useMemo(() => {
    const seen = new Set<string>()
    for (const engine of unifiedEngines) {
      seen.add(engine.stage)
    }
    return [...seen].sort((a, b) => {
      const orderA = STAGE_ORDER[a] ?? 99
      const orderB = STAGE_ORDER[b] ?? 99
      if (orderA !== orderB) return orderA - orderB
      return a.localeCompare(b)
    })
  }, [unifiedEngines])

  // Parse expanded stages from URL
  const expandedStages = useMemo(() => {
    const param = searchParams.get('expanded')
    if (!param) return new Set<string>()
    return new Set(param.split(',').filter((id) => stageIds.includes(id)))
  }, [searchParams, stageIds])

  // Group models by engine_id
  const modelsByEngine = useMemo(() => {
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
    const enginesByStage = new Map<string, UnifiedEngine[]>()
    for (const engine of unifiedEngines) {
      const existing = enginesByStage.get(engine.stage) ?? []
      existing.push(engine)
      enginesByStage.set(engine.stage, existing)
    }

    return stageIds.map((stageId) => {
      const info = STAGE_LABELS[stageId] ?? { label: stageId, description: '' }
      const engines = enginesByStage.get(stageId) ?? []
      const healthyCount = engines.filter(isEngineHealthy).length
      const totalQueueDepth = engines.reduce((sum, e) => sum + e.queue_depth, 0)
      const totalProcessing = engines.reduce((sum, e) => sum + e.processing, 0)
      const totalSessions = engines.reduce((sum, e) => sum + e.active_sessions, 0)
      const totalCapacity = engines.reduce((sum, e) => sum + e.capacity, 0)

      return {
        id: stageId,
        label: info.label,
        description: info.description,
        engines,
        healthyCount,
        totalQueueDepth,
        totalProcessing,
        totalSessions,
        totalCapacity,
      }
    })
  }, [unifiedEngines, stageIds])

  const toggleStage = (stageId: string) => {
    const next = new Set(expandedStages)
    if (next.has(stageId)) {
      next.delete(stageId)
    } else {
      next.add(stageId)
    }
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
  const totalEngines = unifiedEngines.length
  const healthyEngines = unifiedEngines.filter(isEngineHealthy).length
  const totalSessions = unifiedEngines.reduce((sum, e) => sum + e.active_sessions, 0)
  const totalCapacity = unifiedEngines.reduce((sum, e) => sum + e.capacity, 0)

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
                <p className="text-xs text-muted-foreground">{S.engines.engines}</p>
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
              <Users className="h-5 w-5 text-muted-foreground" />
              <div>
                <p className="text-xs text-muted-foreground">{S.engines.sessions}</p>
                <p className="text-lg font-semibold">
                  {totalSessions}
                  <span className="text-muted-foreground text-sm font-normal">/{totalCapacity}</span>
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
                  <span className="text-muted-foreground text-sm font-normal">/{stageIds.length}</span>
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
                  {totalEngines - healthyEngines}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Pipeline */}
      <Card>
        <CardHeader className="flex flex-row items-center gap-2">
          <Server className="h-5 w-5 text-muted-foreground" />
          <CardTitle>{S.engines.pipeline}</CardTitle>
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
                key={stageStatus.id}
                stageStatus={stageStatus}
                isExpanded={expandedStages.has(stageStatus.id)}
                onToggle={() => toggleStage(stageStatus.id)}
                modelsByEngine={modelsByEngine}
              />
            ))
          )}
        </CardContent>
      </Card>
    </div>
  )
}
