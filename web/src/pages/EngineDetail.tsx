import { useMemo } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  Server,
  Globe,
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
import { useModels } from '@/hooks/useModels'
import { cn } from '@/lib/utils'
import type { Engine, BatchEngine } from '@/api/types'

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
            Supported
          </span>
        ) : (
          <span className="flex items-center gap-1 text-sm text-muted-foreground">
            <XCircle className="h-4 w-4" />
            Not supported
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

  // Fetch models and filter by this engine's runtime
  const { data: modelsData } = useModels()
  const engineModels = useMemo(() => {
    return (modelsData?.data ?? []).filter((m) => m.runtime === decodedEngineId)
  }, [modelsData?.data, decodedEngineId])

  // Determine status from either source
  // engineInfo.status is 'running' | 'available' | 'unhealthy'
  // batchEngineInfo.status is 'healthy' | 'unhealthy'
  const status: 'running' | 'available' | 'unhealthy' = engineInfo?.status ?? (batchEngineInfo?.status === 'healthy' ? 'running' : 'unhealthy')
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
          <h1 className="text-2xl font-bold">Engine Not Found</h1>
        </div>
        <Card>
          <CardContent className="py-8">
            <div className="flex flex-col items-center gap-4 text-center">
              <AlertCircle className="h-12 w-12 text-muted-foreground" />
              <div>
                <p className="text-lg font-medium">Engine "{decodedEngineId}" not found</p>
                <p className="text-sm text-muted-foreground mt-1">
                  The engine may not be running or may have been removed.
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
                  {status}
                </Badge>
              </div>
            </div>
            <p className="text-xs sm:text-sm text-muted-foreground mt-1">
              Stage: <span className="font-medium">{stage}</span>
              {engineInfo?.version && (
                <span className="ml-2">· Version: {engineInfo.version}</span>
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
              label="Processing"
              value={batchEngineInfo.processing}
              subValue="active tasks"
            />
            <MetricCard
              icon={Clock}
              label="Queue Depth"
              value={batchEngineInfo.queue_depth}
              subValue="waiting"
            />
          </>
        )}
        {engineInfo?.capabilities.max_audio_duration_s && (
          <MetricCard
            icon={Clock}
            label="Max Duration"
            value={formatDuration(engineInfo.capabilities.max_audio_duration_s)}
          />
        )}
        {engineInfo?.capabilities.max_concurrency && (
          <MetricCard
            icon={Zap}
            label="Max Concurrency"
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
              Capabilities
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-1">
            <CapabilityRow
              label="Word Timestamps"
              supported={engineInfo.capabilities.supports_word_timestamps}
            />
            <CapabilityRow
              label="Streaming"
              supported={engineInfo.capabilities.supports_streaming}
            />
            <CapabilityRow
              label="Max Audio Duration"
              value={formatDuration(engineInfo.capabilities.max_audio_duration_s)}
            />
            {engineInfo.capabilities.max_concurrency && (
              <CapabilityRow
                label="Max Concurrency"
                value={engineInfo.capabilities.max_concurrency}
              />
            )}
          </CardContent>
        </Card>
      )}

      {/* Languages - for transcribe and diarize stages */}
      {engineInfo && (stage === 'transcribe' || stage === 'diarize') && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base font-medium flex items-center gap-2">
              <Globe className="h-4 w-4" />
              Supported Languages
            </CardTitle>
          </CardHeader>
          <CardContent>
            {!engineInfo.capabilities.languages ? (
              <p className="text-sm text-muted-foreground">
                Multilingual — supports automatic language detection and all languages
              </p>
            ) : engineInfo.capabilities.languages.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                No language information available
              </p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {engineInfo.capabilities.languages.map((lang) => (
                  <Badge key={lang} variant="secondary">
                    {lang.toUpperCase()}
                  </Badge>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Models - for transcribe and diarize stages */}
      {engineModels.length > 0 && (stage === 'transcribe' || stage === 'diarize') && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base font-medium flex items-center gap-2">
              <Box className="h-4 w-4" />
              Available Models
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid gap-3 sm:grid-cols-2">
              {engineModels.map((model) => (
                <div
                  key={model.id}
                  className="p-3 rounded-lg border bg-muted/30"
                >
                  <div className="font-medium text-sm">{model.name || model.id}</div>
                  <div className="text-xs text-muted-foreground mt-1">
                    {model.runtime_model_id}
                  </div>
                  {stage === 'transcribe' && (
                    <div className="flex flex-wrap gap-1 mt-2">
                      {model.capabilities.word_timestamps && (
                        <Badge variant="outline" className="text-xs">word timestamps</Badge>
                      )}
                      {model.capabilities.streaming && (
                        <Badge variant="outline" className="text-xs">streaming</Badge>
                      )}
                      {model.size_gb && (
                        <Badge variant="secondary" className="text-xs">{model.size_gb}GB</Badge>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Engine Info */}
      {engineInfo && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base font-medium">Engine Details</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1">
            <CapabilityRow label="Engine ID" value={engineInfo.id} />
            {engineInfo.name && <CapabilityRow label="Name" value={engineInfo.name} />}
            <CapabilityRow label="Version" value={engineInfo.version} />
            <CapabilityRow label="Stage" value={engineInfo.stage} />
            <CapabilityRow label="Status" value={engineInfo.status} />
          </CardContent>
        </Card>
      )}
    </div>
  )
}
