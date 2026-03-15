import { useMemo } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  Clock,
  Zap,
  AlertCircle,
  Layers,
  Activity,
  Box,
  Users,
} from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { BackButton } from '@/components/BackButton'
import { apiClient } from '@/api/client'
import { useModelRegistry } from '@/hooks/useModelRegistry'
import { cn } from '@/lib/utils'
import { S } from '@/lib/strings'
import type { Engine, BatchEngine, EngineStatus, ModelStatus, WorkerStatus } from '@/api/types'

function StatusDot({ status }: { status: 'running' | 'available' | 'unhealthy' }) {
  const colors = {
    running: 'bg-green-500',
    available: 'bg-blue-500',
    unhealthy: 'bg-red-500',
  }
  return <span className={cn('inline-block w-3 h-3 rounded-full shrink-0', colors[status])} />
}

function MetricCard({
  icon: Icon,
  label,
  value,
  subValue,
}: {
  icon: React.ElementType
  label: string
  value: string | number
  subValue?: string
}) {
  return (
    <div className="flex items-center gap-3 p-4 rounded-lg bg-muted/50">
      <Icon className="h-5 w-5 text-muted-foreground" />
      <div>
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className="text-lg font-semibold">{value}</p>
        {subValue && <p className="text-xs text-muted-foreground">{subValue}</p>}
      </div>
    </div>
  )
}

function CapabilityRow({
  label,
  value,
}: {
  label: string
  value?: string | number | null
}) {
  return (
    <div className="flex items-center justify-between py-2 border-b border-border/50 last:border-0">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="text-sm font-medium">{value ?? '-'}</span>
    </div>
  )
}

function formatDuration(seconds: number | null): string {
  if (!seconds) return 'Unlimited'
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`
  return `${(seconds / 3600).toFixed(1)}h`
}

export function EngineDetail() {
  const { engineId } = useParams()
  const decodedEngineId = engineId ? decodeURIComponent(engineId) : ''

  // Fetch discovery API (capabilities)
  const { data: enginesListData, isLoading: isLoadingList } = useQuery({
    queryKey: ['engines-list'],
    queryFn: () => apiClient.getEnginesList(),
    staleTime: 30000,
  })

  // Fetch console API (batch queue stats + realtime workers)
  const { data: consoleEnginesData, isLoading: isLoadingConsole } = useQuery({
    queryKey: ['engines'],
    queryFn: () => apiClient.getEngines(),
    staleTime: 10000,
  })

  const isLoading = isLoadingList || isLoadingConsole

  // Find engine in discovery API
  const engineInfo: Engine | undefined = enginesListData?.engines.find(
    (e) => e.id === decodedEngineId
  )

  // Find engine in console batch API
  const batchEngineInfo: BatchEngine | undefined = consoleEnginesData?.batch_engines.find(
    (e) => e.engine_id === decodedEngineId
  )

  // Find matching realtime workers for this engine
  const realtimeWorkers: WorkerStatus[] = useMemo(() => {
    return (consoleEnginesData?.realtime_engines ?? []).filter(
      (w) => w.engine_id === decodedEngineId
    )
  }, [consoleEnginesData?.realtime_engines, decodedEngineId])

  const totalCapacity = realtimeWorkers.reduce((sum, w) => sum + w.capacity, 0)
  const totalSessions = realtimeWorkers.reduce((sum, w) => sum + w.active_sessions, 0)
  const utilization = totalCapacity > 0 ? Math.round((totalSessions / totalCapacity) * 100) : 0

  // Fetch models from registry for this engine
  const { data: registryData } = useModelRegistry({ engine_id: decodedEngineId })
  const engineModels = useMemo(() => registryData?.data ?? [], [registryData?.data])

  // Capability summaries from models
  const capSummary = useMemo(() => {
    const total = engineModels.length
    const wordTs = engineModels.filter((m) => m.word_timestamps).length
    const streaming = engineModels.filter((m) => m.native_streaming).length
    return { total, wordTs, streaming }
  }, [engineModels])

  // Status mapping
  function batchStatusToDiscovery(s: EngineStatus): 'running' | 'available' | 'unhealthy' {
    switch (s) {
      case 'idle':
      case 'processing':
      case 'loading':
      case 'downloading':
        return 'running'
      case 'stale':
      case 'error':
      case 'offline':
        return 'unhealthy'
    }
  }

  function engineStatusLabel(s: EngineStatus): string {
    switch (s) {
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

  const status: 'running' | 'available' | 'unhealthy' = batchEngineInfo
    ? batchStatusToDiscovery(batchEngineInfo.status)
    : (engineInfo?.status ?? 'unhealthy')
  const statusLabel = batchEngineInfo ? engineStatusLabel(batchEngineInfo.status) : status
  const stage = engineInfo?.stage ?? batchEngineInfo?.stage ?? 'unknown'

  if (isLoading) {
    return (
      <div className="space-y-6">
        <div className="flex items-center gap-4">
          <BackButton fallbackPath="/engines" />
          <div className="h-8 w-48 bg-muted animate-pulse rounded" />
        </div>
        <div className="grid gap-4 grid-cols-2 sm:grid-cols-4">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="h-20 bg-muted animate-pulse rounded-lg" />
          ))}
        </div>
      </div>
    )
  }

  if (!engineInfo && !batchEngineInfo) {
    return (
      <div className="space-y-6">
        <div className="flex items-center gap-4">
          <BackButton fallbackPath="/engines" />
          <h1 className="text-2xl font-bold">{S.engineDetail.notFound}</h1>
        </div>
        <Card>
          <CardContent className="py-8">
            <div className="flex flex-col items-center gap-4 text-center">
              <AlertCircle className="h-12 w-12 text-muted-foreground" />
              <div>
                <p className="text-lg font-medium">Engine "{decodedEngineId}" not found</p>
                <p className="text-sm text-muted-foreground mt-1">
                  {S.engineDetail.notFoundHint}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3 sm:gap-4">
          <BackButton fallbackPath="/engines" />
          <div className="min-w-0 flex-1">
            <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-3">
              <h1 className="text-lg sm:text-2xl font-bold truncate">{decodedEngineId}</h1>
              <div className="flex items-center gap-2">
                <StatusDot status={status} />
                <Badge
                  variant={status === 'running' || status === 'available' ? 'success' : 'destructive'}
                >
                  {statusLabel}
                </Badge>
              </div>
            </div>
            <p className="text-xs sm:text-sm text-muted-foreground mt-1">
              {S.engineDetail.stageLabel} <span className="font-medium">{stage}</span>
              {engineInfo?.version && (
                <span className="ml-2">· {S.engineDetail.versionLabel} {engineInfo.version}</span>
              )}
            </p>
          </div>
        </div>
      </div>

      {/* Quick Stats — batch + realtime */}
      <div className="grid gap-4 grid-cols-2 sm:grid-cols-4">
        <MetricCard
          icon={Layers}
          label="Stage"
          value={stage}
        />
        {batchEngineInfo && (
          <>
            <MetricCard
              icon={Activity}
              label={S.engineDetail.processingMetric}
              value={batchEngineInfo.processing}
              subValue={S.engineDetail.activeTasks}
            />
            <MetricCard
              icon={Clock}
              label={S.engineDetail.queueDepth}
              value={batchEngineInfo.queue_depth}
              subValue={S.engineDetail.waiting}
            />
          </>
        )}
        {engineInfo?.capabilities.max_concurrency && (
          <MetricCard
            icon={Zap}
            label={S.engineDetail.maxConcurrency}
            value={engineInfo.capabilities.max_concurrency}
          />
        )}
      </div>

      {/* Realtime Utilization */}
      {totalCapacity > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base font-medium flex items-center gap-2">
              <Users className="h-4 w-4" />
              Session Utilization
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">
                  {totalSessions} active / {totalCapacity} capacity
                  {realtimeWorkers.length > 1 && ` (${realtimeWorkers.length} workers)`}
                </span>
                <span className="font-medium">{utilization}%</span>
              </div>
              <div className="h-3 w-full bg-muted rounded-full overflow-hidden">
                <div
                  className={cn(
                    'h-full rounded-full transition-all',
                    utilization > 80 ? 'bg-yellow-500' : 'bg-green-500'
                  )}
                  style={{ width: `${Math.min(utilization, 100)}%` }}
                />
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Models with per-model capabilities */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base font-medium flex items-center gap-2">
            <Box className="h-4 w-4" />
            {S.engineDetail.availableModels}
            {capSummary.total > 0 && (
              <span className="text-sm font-normal text-muted-foreground ml-2">
                {capSummary.wordTs}/{capSummary.total} word timestamps · {capSummary.streaming}/{capSummary.total} streaming
              </span>
            )}
          </CardTitle>
        </CardHeader>
        <CardContent>
          {engineModels.length === 0 ? (
            <p className="text-sm text-muted-foreground italic">
              {S.engineDetail.noModelsForEngine}
            </p>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              {engineModels.map((model) => {
                const statusColors: Record<ModelStatus, string> = {
                  ready: 'bg-green-500',
                  downloading: 'bg-zinc-400',
                  not_downloaded: 'bg-zinc-400',
                  failed: 'bg-zinc-400',
                }
                const statusLabels: Record<ModelStatus, string> = {
                  ready: 'Ready',
                  downloading: 'Not Downloaded',
                  not_downloaded: 'Not Downloaded',
                  failed: 'Not Downloaded',
                }
                const sizeGb = model.size_bytes ? (model.size_bytes / 1e9).toFixed(1) : null

                return (
                  <div
                    key={model.id}
                    className={cn(
                      'p-3 rounded-lg border bg-muted/30',
                      model.status === 'ready' && 'border-green-500/30'
                    )}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0 flex-1">
                        <div className="font-medium text-sm truncate">{model.name || model.id}</div>
                        <div className="text-xs text-muted-foreground mt-1 truncate">
                          {model.loaded_model_id}
                        </div>
                      </div>
                      <div className="flex items-center gap-2 flex-shrink-0">
                        <span
                          className={cn('w-2 h-2 rounded-full', statusColors[model.status])}
                          title={statusLabels[model.status]}
                        />
                        <span className="text-xs text-muted-foreground">
                          {statusLabels[model.status]}
                        </span>
                      </div>
                    </div>
                    <div className="flex flex-wrap gap-1 mt-2">
                      {model.word_timestamps && (
                        <Badge variant="outline" className="text-xs">word timestamps</Badge>
                      )}
                      {model.native_streaming && (
                        <Badge variant="outline" className="text-xs">streaming</Badge>
                      )}
                      {sizeGb && (
                        <Badge variant="secondary" className="text-xs">{sizeGb}GB</Badge>
                      )}
                      {!model.supports_cpu && (
                        <Badge variant="outline" className="text-xs text-amber-600 border-amber-400">GPU only</Badge>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Engine Info */}
      {engineInfo && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base font-medium">{S.engineDetail.engineDetails}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1">
            <CapabilityRow label="Engine ID" value={engineInfo.id} />
            {engineInfo.name && <CapabilityRow label="Name" value={engineInfo.name} />}
            <CapabilityRow label="Version" value={engineInfo.version} />
            <CapabilityRow label="Stage" value={engineInfo.stage} />
            <CapabilityRow label="Status" value={statusLabel} />
            {engineInfo.capabilities.max_audio_duration_s && (
              <CapabilityRow label={S.engineDetail.maxAudioDuration} value={formatDuration(engineInfo.capabilities.max_audio_duration_s)} />
            )}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
