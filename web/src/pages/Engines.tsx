import { useState, useMemo } from 'react'
import { Link } from 'react-router-dom'
import {
  ChevronDown,
  ChevronRight,
  Server,
  Radio,
  AlertCircle,
  CheckCircle,
  XCircle,
  Layers,
  Box,
} from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { useEngines } from '@/hooks/useEngines'
import { useModels } from '@/hooks/useModels'
import type { BatchEngine, WorkerStatus, Model } from '@/api/types'
import { cn } from '@/lib/utils'

// Pipeline stages in their natural processing order
const PIPELINE_STAGES = [
  { id: 'prepare', label: 'Prepare', description: 'Audio preprocessing' },
  { id: 'transcribe', label: 'Transcribe', description: 'Speech-to-text' },
  { id: 'align', label: 'Align', description: 'Word-level timestamps' },
  { id: 'diarize', label: 'Diarize', description: 'Speaker identification' },
  { id: 'pii-detect', label: 'PII Detect', description: 'Sensitive data detection' },
  { id: 'audio-redact', label: 'Audio Redact', description: 'PII audio masking' },
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

function StatusDot({ status }: { status: 'healthy' | 'unhealthy' | 'warning' | 'empty' }) {
  const colors = {
    healthy: 'bg-green-500',
    unhealthy: 'bg-red-500',
    warning: 'bg-yellow-500',
    empty: 'bg-zinc-500',
  }
  return <span className={cn('inline-block w-2 h-2 rounded-full shrink-0', colors[status])} />
}

function isEngineHealthy(engine: BatchEngine): boolean {
  // API may return 'healthy', 'running', 'idle', etc. - treat anything except 'unhealthy' as healthy
  return engine.status !== 'unhealthy'
}

function getStageAggregateStatus(engines: BatchEngine[]): 'healthy' | 'unhealthy' | 'warning' | 'empty' {
  if (engines.length === 0) return 'empty'
  const healthy = engines.filter(isEngineHealthy).length
  const unhealthy = engines.length - healthy
  if (unhealthy === engines.length) return 'unhealthy'
  if (unhealthy > 0) return 'warning'
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
  const { stage, engines, unhealthyCount, totalQueueDepth, totalProcessing } = stageStatus
  const aggregateStatus = getStageAggregateStatus(engines)

  const summaryParts: string[] = []
  if (engines.length === 0) {
    summaryParts.push('no engines')
  } else {
    summaryParts.push(`${engines.length} engine${engines.length !== 1 ? 's' : ''}`)
    if (totalQueueDepth > 0 || totalProcessing > 0) {
      const activityParts: string[] = []
      if (totalProcessing > 0) activityParts.push(`${totalProcessing} processing`)
      if (totalQueueDepth > 0) activityParts.push(`${totalQueueDepth} queued`)
      summaryParts.push(activityParts.join(', '))
    }
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
        {aggregateStatus === 'warning' && (
          <Badge variant="warning" className="hidden sm:inline-flex">
            {unhealthyCount} unhealthy
          </Badge>
        )}
        <span className="text-sm text-muted-foreground">{summaryParts.join(' · ')}</span>
      </div>
    </button>
  )
}

// Stage-specific info to show in engine cards
function getStageSpecificInfo(stage: string, models: Model[]): React.ReactNode {
  switch (stage) {
    case 'transcribe':
      // Show models for transcription engines
      if (models.length === 0) return null
      return (
        <div className="mt-3 flex items-center gap-2">
          <Box className="h-3 w-3 text-muted-foreground shrink-0" />
          <div className="flex flex-wrap gap-1">
            {models.slice(0, 3).map((model) => (
              <Badge key={model.id} variant="secondary" className="text-xs">
                {model.name || model.id}
              </Badge>
            ))}
            {models.length > 3 && (
              <Badge variant="outline" className="text-xs">
                +{models.length - 3} more
              </Badge>
            )}
          </div>
        </div>
      )
    case 'diarize':
      // Show models for diarization (if available)
      if (models.length === 0) return null
      return (
        <div className="mt-3 flex items-center gap-2">
          <Box className="h-3 w-3 text-muted-foreground shrink-0" />
          <div className="flex flex-wrap gap-1">
            {models.slice(0, 2).map((model) => (
              <Badge key={model.id} variant="secondary" className="text-xs">
                {model.name || model.id}
              </Badge>
            ))}
            {models.length > 2 && (
              <Badge variant="outline" className="text-xs">
                +{models.length - 2} more
              </Badge>
            )}
          </div>
        </div>
      )
    default:
      // Other stages: don't show models
      return null
  }
}

function EngineCard({ engine, models }: { engine: BatchEngine; models: Model[] }) {
  const isHealthy = isEngineHealthy(engine)
  const hasActivity = engine.processing > 0 || engine.queue_depth > 0

  return (
    <Link
      to={`/engines/${encodeURIComponent(engine.engine_id)}`}
      className={cn(
        'block p-4 rounded-lg border transition-all',
        'hover:border-primary/50 hover:bg-accent/30',
        'focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2'
      )}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-3 min-w-0">
          <StatusDot status={isHealthy ? 'healthy' : 'unhealthy'} />
          <div className="min-w-0">
            <div className="font-medium truncate">{engine.engine_id}</div>
            <div className="text-sm text-muted-foreground flex items-center gap-2">
              {isHealthy ? (
                <span className="flex items-center gap-1">
                  <CheckCircle className="h-3 w-3 text-green-500" />
                  Healthy
                </span>
              ) : (
                <span className="flex items-center gap-1">
                  <XCircle className="h-3 w-3 text-red-500" />
                  Unhealthy
                </span>
              )}
            </div>
          </div>
        </div>
        {hasActivity && (
          <div className="text-right shrink-0">
            {engine.processing > 0 && (
              <div className="text-sm">
                <span className="font-medium">{engine.processing}</span>
                <span className="text-muted-foreground ml-1">processing</span>
              </div>
            )}
            {engine.queue_depth > 0 && (
              <div className="text-sm">
                <span className="font-medium">{engine.queue_depth}</span>
                <span className="text-muted-foreground ml-1">queued</span>
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
  modelsByRuntime: Map<string, Model[]>
}) {
  return (
    <div className="border rounded-lg overflow-hidden">
      <StageHeader stageStatus={stageStatus} isExpanded={isExpanded} onToggle={onToggle} />
      {isExpanded && stageStatus.engines.length > 0 && (
        <div className="p-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
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
            No engines registered for this stage
          </div>
        </div>
      )}
    </div>
  )
}

function RealtimeWorkerCard({ worker }: { worker: WorkerStatus }) {
  const isReady = worker.status === 'ready'
  const utilization = worker.capacity > 0 ? (worker.active_sessions / worker.capacity) * 100 : 0

  return (
    <div
      className={cn(
        'p-4 rounded-lg border',
        isReady ? 'border-border' : 'border-red-500/30 bg-red-500/5'
      )}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-center gap-3 min-w-0">
          <StatusDot status={isReady ? 'healthy' : 'unhealthy'} />
          <div className="min-w-0">
            <div className="font-medium truncate">{worker.worker_id}</div>
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
      {worker.models.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1">
          {worker.models.map((model) => (
            <Badge key={model} variant="secondary" className="text-xs">
              {model}
            </Badge>
          ))}
        </div>
      )}
    </div>
  )
}

export function Engines() {
  const { data, isLoading, error } = useEngines()
  const { data: modelsData } = useModels()
  const [expandedStages, setExpandedStages] = useState<Set<StageId>>(new Set())

  const batchEngines = useMemo(() => data?.batch_engines ?? [], [data?.batch_engines])
  const realtimeWorkers = data?.realtime_engines ?? []

  // Group models by runtime (engine)
  const modelsByRuntime = useMemo(() => {
    const map = new Map<string, Model[]>()
    for (const model of modelsData?.data ?? []) {
      const existing = map.get(model.runtime) ?? []
      existing.push(model)
      map.set(model.runtime, existing)
    }
    return map
  }, [modelsData?.data])

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

  // Auto-expand stages with issues or activity
  const toggleStage = (stageId: StageId) => {
    setExpandedStages((prev) => {
      const next = new Set(prev)
      if (next.has(stageId)) {
        next.delete(stageId)
      } else {
        next.add(stageId)
      }
      return next
    })
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
        <h1 className="text-2xl font-bold">Engines</h1>
        <p className="text-muted-foreground">
          Pipeline stages and processing capacity
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
                <p className="text-xs text-muted-foreground">Batch Engines</p>
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
                <p className="text-xs text-muted-foreground">Real-time Workers</p>
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
                <p className="text-xs text-muted-foreground">Pipeline Stages</p>
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
                <p className="text-xs text-muted-foreground">Issues</p>
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
          <CardTitle>Batch Pipeline</CardTitle>
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
          <CardTitle>Real-time Workers</CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {[1, 2].map((i) => (
                <div key={i} className="h-24 bg-muted animate-pulse rounded-lg" />
              ))}
            </div>
          ) : realtimeWorkers.length === 0 ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground p-4 rounded-lg bg-muted/30 border border-dashed">
              <AlertCircle className="h-4 w-4" />
              No real-time workers registered
            </div>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {realtimeWorkers.map((worker) => (
                <RealtimeWorkerCard key={worker.worker_id} worker={worker} />
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
