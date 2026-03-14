import { useMemo } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  Server,
  Clock,
  Zap,
  CheckCircle,
  XCircle,
  AlertCircle,
  Layers,
  Activity,
  Box,
} from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { BackButton } from '@/components/BackButton'
import { apiClient } from '@/api/client'
import { useModelRegistry } from '@/hooks/useModelRegistry'
import { cn } from '@/lib/utils'
import { S } from '@/lib/strings'
import type { Engine, BatchEngine, EngineStatus, ModelStatus } from '@/api/types'

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
  supported,
}: {
  label: string
  value?: string | number | null
  supported?: boolean
}) {
  if (supported !== undefined) {
    return (
      <div className="flex items-center justify-between py-2 border-b border-border/50 last:border-0">
        <span className="text-sm text-muted-foreground">{label}</span>
        {supported ? (
          <span className="flex items-center gap-1 text-sm text-green-500">
            <CheckCircle className="h-4 w-4" />
            {S.engineDetail.supported}
          </span>
        ) : (
          <span className="flex items-center gap-1 text-sm text-muted-foreground">
            <XCircle className="h-4 w-4" />
            {S.engineDetail.notSupported}
          </span>
        )}
      </div>
    )
  }

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

  // Fetch both engine lists to find the engine
  const { data: enginesListData, isLoading: isLoadingList } = useQuery({
    queryKey: ['engines-list'],
    queryFn: () => apiClient.getEnginesList(),
    staleTime: 30000,
  })

  const { data: consoleEnginesData, isLoading: isLoadingConsole } = useQuery({
    queryKey: ['engines'],
    queryFn: () => apiClient.getEngines(),
    staleTime: 10000,
  })

  const isLoading = isLoadingList || isLoadingConsole

  // Find the engine in the discovery API (has capabilities)
  const engineInfo: Engine | undefined = enginesListData?.engines.find(
    (e) => e.id === decodedEngineId
  )

  // Find the engine in console API (has queue stats)
  const batchEngineInfo: BatchEngine | undefined = consoleEnginesData?.batch_engines.find(
    (e) => e.engine_id === decodedEngineId
  )

  // Fetch models from registry and filter by this engine's engine_id
  const { data: registryData } = useModelRegistry({ engine_id: decodedEngineId })
  const engineModels = useMemo(() => {
    return registryData?.data ?? []
  }, [registryData?.data])

  // Determine status - prefer console API (heartbeat-based) over discovery API
  // batchEngineInfo.status is EngineStatus (from console API heartbeats) - more accurate
  // engineInfo.status is 'running' | 'available' | 'unhealthy' (from discovery API)
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

  // Human-readable label for granular status
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

  // Prefer batch status (from heartbeats) as it's more accurate than discovery API
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

      {/* Quick Stats */}
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
        {engineInfo?.capabilities.max_audio_duration_s && (
          <MetricCard
            icon={Clock}
            label={S.engineDetail.maxDuration}
            value={formatDuration(engineInfo.capabilities.max_audio_duration_s)}
          />
        )}
        {engineInfo?.capabilities.max_concurrency && (
          <MetricCard
            icon={Zap}
            label={S.engineDetail.maxConcurrency}
            value={engineInfo.capabilities.max_concurrency}
          />
        )}
      </div>

      {/* Capabilities - only for transcribe stage */}
      {engineInfo && stage === 'transcribe' && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base font-medium flex items-center gap-2">
              <Server className="h-4 w-4" />
              {S.engineDetail.capabilities}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-1">
            <CapabilityRow
              label={S.engineDetail.wordTimestamps}
              supported={engineInfo.capabilities.supports_word_timestamps}
            />
            <CapabilityRow
              label={S.engineDetail.streaming}
              supported={engineInfo.capabilities.supports_native_streaming}
            />
            <CapabilityRow
              label={S.engineDetail.maxAudioDuration}
              value={formatDuration(engineInfo.capabilities.max_audio_duration_s)}
            />
            {engineInfo.capabilities.max_concurrency && (
              <CapabilityRow
                label={S.engineDetail.maxConcurrency}
                value={engineInfo.capabilities.max_concurrency}
              />
            )}
          </CardContent>
        </Card>
      )}


      {/* Models - only for transcribe stage (diarize doesn't use model registry) */}
      {stage === 'transcribe' && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base font-medium flex items-center gap-2">
              <Box className="h-4 w-4" />
              {S.engineDetail.availableModels}
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
                  // Model status styling (only ready vs not ready matters on this page)
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
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </CardContent>
        </Card>
      )}

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
          </CardContent>
        </Card>
      )}
    </div>
  )
}
