import { useMemo } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  Radio,
  Zap,
  CheckCircle,
  XCircle,
  AlertCircle,
  Activity,
  Box,
  Users,
  Cpu,
} from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { BackButton } from '@/components/BackButton'
import { apiClient } from '@/api/client'
import { useModelRegistry } from '@/hooks/useModelRegistry'
import { cn } from '@/lib/utils'
import type { WorkerStatus, ModelStatus } from '@/api/types'

function StatusDot({ status }: { status: 'ready' | 'unhealthy' }) {
  const colors = {
    ready: 'bg-green-500',
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
            {value || 'Supported'}
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

export function RealtimeWorkerDetail() {
  const { workerId } = useParams()
  const decodedWorkerId = workerId ? decodeURIComponent(workerId) : ''

  // Fetch engines data (includes realtime workers)
  const { data: enginesData, isLoading } = useQuery({
    queryKey: ['engines'],
    queryFn: () => apiClient.getEngines(),
    staleTime: 10000,
  })

  // Find the worker
  const worker: WorkerStatus | undefined = enginesData?.realtime_engines.find(
    (w) => w.instance === decodedWorkerId
  )

  // Fetch models from registry filtered by this worker's engine_id
  const { data: registryData } = useModelRegistry({ engine_id: worker?.engine_id ?? undefined })
  const availableModels = useMemo(() => {
    return registryData?.data ?? []
  }, [registryData?.data])

  // Model status styling
  const statusColors: Record<ModelStatus, string> = {
    ready: 'bg-green-500',
    downloading: 'bg-yellow-500',
    not_downloaded: 'bg-zinc-400',
    failed: 'bg-red-500',
  }
  const statusLabels: Record<ModelStatus, string> = {
    ready: 'Ready',
    downloading: 'Downloading',
    not_downloaded: 'Not Downloaded',
    failed: 'Failed',
  }

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

  if (!worker) {
    return (
      <div className="space-y-6">
        <div className="flex items-center gap-4">
          <BackButton fallbackPath="/engines" />
          <h1 className="text-2xl font-bold">Worker Not Found</h1>
        </div>
        <Card>
          <CardContent className="py-8">
            <div className="flex flex-col items-center gap-4 text-center">
              <AlertCircle className="h-12 w-12 text-muted-foreground" />
              <div>
                <p className="text-lg font-medium">Worker "{decodedWorkerId}" not found</p>
                <p className="text-sm text-muted-foreground mt-1">
                  The worker may not be running or may have been removed.
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    )
  }

  const isReady = worker.status === 'ready'
  const utilization = worker.capacity > 0 ? Math.round((worker.active_sessions / worker.capacity) * 100) : 0
  const readyModelsCount = availableModels.filter((m) => m.status === 'ready').length

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3 sm:gap-4">
          <BackButton fallbackPath="/engines" />
          <div className="min-w-0 flex-1">
            <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-3">
              <h1 className="text-lg sm:text-2xl font-bold truncate">{decodedWorkerId}</h1>
              <div className="flex items-center gap-2">
                <StatusDot status={worker.status} />
                <Badge variant={isReady ? 'success' : 'destructive'}>
                  {isReady ? 'Ready' : 'Unhealthy'}
                </Badge>
                {worker.engine_id && (
                  <Badge variant="outline">{worker.engine_id}</Badge>
                )}
              </div>
            </div>
            <p className="text-xs sm:text-sm text-muted-foreground mt-1 truncate">
              {worker.endpoint}
            </p>
          </div>
        </div>
      </div>

      {/* Quick Stats */}
      <div className="grid gap-4 grid-cols-2 sm:grid-cols-4">
        <MetricCard
          icon={Users}
          label="Active Sessions"
          value={worker.active_sessions}
          subValue={`of ${worker.capacity} capacity`}
        />
        <MetricCard
          icon={Activity}
          label="Utilization"
          value={`${utilization}%`}
        />
        <MetricCard
          icon={Zap}
          label="Capacity"
          value={worker.capacity}
          subValue="max sessions"
        />
        <MetricCard
          icon={Box}
          label="Loaded Models"
          value={worker.models.length}
        />
      </div>

      {/* Utilization Bar */}
      {worker.capacity > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base font-medium flex items-center gap-2">
              <Activity className="h-4 w-4" />
              Session Utilization
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">
                  {worker.active_sessions} active / {worker.capacity} capacity
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

      {/* Currently Loaded Models */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base font-medium flex items-center gap-2">
            <Cpu className="h-4 w-4" />
            Loaded Models
            <Badge variant="secondary" className="ml-2">{worker.models.length}</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {worker.models.length === 0 ? (
            <p className="text-sm text-muted-foreground italic">
              No models currently loaded on this worker
            </p>
          ) : (
            <div className="flex flex-wrap gap-2">
              {worker.models.map((model) => (
                <Badge key={model} variant="secondary" className="text-sm">
                  {model}
                </Badge>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Available Models (from registry) */}
      {worker.engine_id && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base font-medium flex items-center gap-2">
              <Box className="h-4 w-4" />
              Available Models
              <span className="text-sm font-normal text-muted-foreground ml-2">
                ({readyModelsCount} ready / {availableModels.length} total)
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent>
            {availableModels.length === 0 ? (
              <p className="text-sm text-muted-foreground italic">
                No models in registry for engine_id "{worker.engine_id}"
              </p>
            ) : (
              <div className="grid gap-3 sm:grid-cols-2">
                {availableModels.map((model) => {
                  const isLoaded = worker.models.includes(model.loaded_model_id) ||
                    worker.models.includes(model.id) ||
                    worker.models.includes(model.name || '')
                  const sizeGb = model.size_bytes ? (model.size_bytes / 1e9).toFixed(1) : null

                  return (
                    <div
                      key={model.id}
                      className={cn(
                        'p-3 rounded-lg border bg-muted/30',
                        model.status === 'ready' && 'border-green-500/30',
                        isLoaded && 'ring-2 ring-primary/50'
                      )}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <span className="font-medium text-sm truncate">
                              {model.name || model.id}
                            </span>
                            {isLoaded && (
                              <Badge variant="default" className="text-xs shrink-0">
                                Loaded
                              </Badge>
                            )}
                          </div>
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

      {/* Capabilities */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base font-medium flex items-center gap-2">
            <Radio className="h-4 w-4" />
            Capabilities
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-1">
          <CapabilityRow
            label="Vocabulary Support"
            supported={worker.vocabulary_support?.realtime ?? false}
            value={worker.vocabulary_support?.method && worker.vocabulary_support.method !== 'none'
              ? worker.vocabulary_support.method.replace('_', ' ')
              : undefined}
          />
          <CapabilityRow
            label="Runtime"
            value={worker.engine_id ?? 'Unknown'}
          />
          <CapabilityRow
            label="Max Sessions"
            value={worker.capacity}
          />
        </CardContent>
      </Card>


      {/* Worker Details */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base font-medium">Worker Details</CardTitle>
        </CardHeader>
        <CardContent className="space-y-1">
          <CapabilityRow label="Instance" value={worker.instance} />
          <CapabilityRow label="Endpoint" value={worker.endpoint} />
          <CapabilityRow label="Runtime" value={worker.engine_id ?? 'Unknown'} />
          <CapabilityRow label="Status" value={isReady ? 'Ready' : 'Unhealthy'} />
        </CardContent>
      </Card>
    </div>
  )
}
