import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { StatusDot, type DotStatus } from '@/components/StatusDot'
import { AutoscalerStrip } from '@/components/AutoscalerStrip'
import { useNodes } from '@/hooks/useNodes'
import { useNowMs } from '@/hooks/useNowMs'
import type { NodeView, NodeEngine } from '@/api/types'
import { cn } from '@/lib/utils'
import { S } from '@/lib/strings'
import { formatElapsed } from '@/lib/time'
import { stagePillClass, STAGE_ORDER } from '@/lib/stages'
import { AlertCircle, Loader2, Network, Server } from 'lucide-react'

/** Sort rank for an engine by its pipeline stage. Unknown stages go last. */
function stageRank(stage: string): number {
  return STAGE_ORDER[stage] ?? 99
}

function engineDotStatus(engine: NodeEngine): DotStatus {
  if (!engine.is_healthy) return 'unhealthy'
  if (engine.status === 'processing' || engine.status === 'busy') return 'warning'
  return 'healthy'
}

function engineStatusLabel(status: string, isHealthy: boolean): string {
  if (!isHealthy) return 'Offline'
  switch (status) {
    case 'idle':
    case 'ready':
      return 'Idle'
    case 'processing':
    case 'busy':
      return 'Busy'
    default:
      return status.charAt(0).toUpperCase() + status.slice(1)
  }
}

// GPU progress bar
function GpuBar({ usedGb, totalGb }: { usedGb: number; totalGb: number }) {
  if (totalGb <= 0) return null

  const pct = Math.min((usedGb / totalGb) * 100, 100)
  const barColor = pct > 90 ? 'bg-red-500' : pct > 75 ? 'bg-amber-500' : 'bg-green-500'

  return (
    <div className="mt-3">
      <div className="flex justify-between text-xs text-muted-foreground mb-1">
        <span>{S.infrastructure.gpuMemory}</span>
        <span>
          {usedGb.toFixed(1)} / {totalGb.toFixed(1)} GB
        </span>
      </div>
      <div className="h-2 bg-muted rounded-full overflow-hidden">
        <div className={cn('h-full rounded-full transition-all', barColor)} style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}

// Deploy environment badge
function DeployBadge({ node }: { node: NodeView }) {
  if (node.deploy_env === 'aws') {
    return (
      <Badge variant="warning" className="text-xs">
        {S.infrastructure.aws}
        {node.aws_az ? ` \u00b7 ${node.aws_az}` : ''}
        {node.aws_instance_type ? ` \u00b7 ${node.aws_instance_type}` : ''}
      </Badge>
    )
  }
  return (
    <Badge variant="secondary" className="text-xs">
      {S.infrastructure.localDev}
    </Badge>
  )
}

function interfaceLabel(interfaces: string[]): string {
  const hasBatch = interfaces.includes('batch')
  const hasRealtime = interfaces.includes('realtime')
  if (hasBatch && hasRealtime) return 'b+rt'
  if (hasRealtime) return 'rt'
  return 'batch'
}

// Engine row inside a node card
function EngineRow({ engine }: { engine: NodeEngine }) {
  const dot = engineDotStatus(engine)
  const label = engineStatusLabel(engine.status, engine.is_healthy)
  const iface = interfaceLabel(engine.interfaces)
  const active = engine.active_batch + engine.active_realtime

  return (
    <div className="flex items-center justify-between gap-3 py-2 px-1">
      <div className="flex items-center gap-2 min-w-0 flex-1">
        <Badge className={cn('text-xs font-normal border-0 shrink-0', stagePillClass(engine.stage))}>
          {engine.stage}
        </Badge>
        <span className="text-sm font-medium truncate" title={engine.engine_id}>
          {engine.engine_id}
        </span>
      </div>
      <div className="flex items-center gap-2.5 shrink-0">
        <span className="rounded bg-muted px-1 py-0.5 text-[10px] leading-none text-muted-foreground">
          {iface}
        </span>
        <span className="flex items-center gap-1.5" title={label}>
          <StatusDot status={dot} />
          {!engine.is_healthy && <span className="text-xs text-red-400">{label}</span>}
        </span>
        <span className="text-xs text-muted-foreground tabular-nums text-right">
          {active} / {engine.capacity}
        </span>
      </div>
    </div>
  )
}

// M91.7: shadow card for a worker the autoscaler launched but whose
// engines have not registered yet. Flips to a real NodeCard (same
// node_id) once heartbeats appear.
function BootingNodeCard({ node }: { node: NodeView }) {
  const nowMs = useNowMs()
  const elapsedS =
    node.booting_since && nowMs !== null
      ? Math.floor((nowMs - Date.parse(node.booting_since)) / 1000)
      : null
  const stuck =
    elapsedS !== null && node.boot_timeout_s !== null && elapsedS > node.boot_timeout_s

  return (
    <Card className={cn('border-dashed', stuck ? 'border-red-500/50' : 'border-amber-500/40')}>
      <CardHeader className="pb-2">
        <div className="flex items-center gap-2">
          <Loader2
            className={cn(
              'h-4 w-4 shrink-0',
              stuck ? 'text-red-400' : 'animate-spin text-amber-500',
            )}
          />
          <div className="min-w-0">
            <CardTitle className="text-base font-medium truncate">{node.node_id}</CardTitle>
            <Badge variant="warning" className="text-xs">
              {S.infrastructure.booting}
              {node.aws_instance_type ? ` · ${node.aws_instance_type}` : ''}
              {node.shape ? ` · ${node.shape}` : ''}
            </Badge>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <p className={cn('text-sm', stuck ? 'text-red-400' : 'text-muted-foreground')}>
          {stuck ? S.infrastructure.stuckBooting : S.infrastructure.bootingHint}
        </p>
        {node.booting_since && nowMs !== null && (
          <p className="text-xs text-muted-foreground mt-1 tabular-nums">
            {formatElapsed(node.booting_since, nowMs)}
          </p>
        )}
      </CardContent>
    </Card>
  )
}

// Node card
function NodeCard({ node }: { node: NodeView }) {
  const hasGpu = node.gpu_memory_total_gb > 0
  const sortedEngines = [...node.engines].sort((a, b) => {
    const stageCmp = stageRank(a.stage) - stageRank(b.stage)
    if (stageCmp !== 0) return stageCmp
    const nameCmp = a.engine_id.localeCompare(b.engine_id)
    if (nameCmp !== 0) return nameCmp
    // realtime before batch for the same engine
    const aRt = a.interfaces.includes('realtime') ? 0 : 1
    const bRt = b.interfaces.includes('realtime') ? 0 : 1
    return aRt - bRt
  })

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center gap-2">
          <Server className="h-4 w-4 text-muted-foreground shrink-0" />
          <div className="min-w-0">
            <CardTitle className="text-base font-medium truncate">
              {node.node_id}
            </CardTitle>
            <DeployBadge node={node} />
          </div>
        </div>
        {!hasGpu && (
          <span className="text-xs text-muted-foreground">{S.infrastructure.cpuOnly}</span>
        )}
      </CardHeader>
      <CardContent>
        <GpuBar usedGb={node.gpu_memory_used_gb} totalGb={node.gpu_memory_total_gb} />
        <div className={cn('divide-y divide-border', hasGpu && 'mt-3')}>
          {sortedEngines.map((engine) => (
            <EngineRow key={engine.instance} engine={engine} />
          ))}
        </div>
      </CardContent>
    </Card>
  )
}

// Loading skeleton
function LoadingSkeleton() {
  return (
    <div className="grid gap-4 grid-cols-[repeat(auto-fill,minmax(min(360px,100%),1fr))]">
      {[1, 2, 3].map((i) => (
        <Card key={i}>
          <CardHeader className="pb-2">
            <Skeleton className="h-5 w-48" />
          </CardHeader>
          <CardContent>
            <Skeleton className="h-2 w-full mt-3 mb-4" />
            <div className="space-y-3">
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-3/4" />
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  )
}

// Empty state
function EmptyState() {
  return (
    <Card>
      <CardContent className="flex flex-col items-center justify-center py-12">
        <Network className="h-12 w-12 text-muted-foreground mb-4" />
        <p className="text-lg font-medium">{S.infrastructure.noNodes}</p>
        <p className="text-sm text-muted-foreground mt-1">{S.infrastructure.noNodesHint}</p>
      </CardContent>
    </Card>
  )
}

export function Infrastructure() {
  const { data, isLoading, error } = useNodes()

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold">{S.infrastructure.title}</h2>
        <p className="text-muted-foreground">{S.infrastructure.subtitle}</p>
      </div>

      {error && (
        <Card className="border-red-500/30 bg-red-500/5">
          <CardContent className="flex items-center gap-3 py-4">
            <AlertCircle className="h-5 w-5 text-red-400" />
            <span className="text-sm text-red-400">Failed to load infrastructure data</span>
          </CardContent>
        </Card>
      )}

      {isLoading && <LoadingSkeleton />}

      {data && data.autoscaler.length > 0 && (
        <div className="space-y-3">
          {data.autoscaler.map((view) => (
            <AutoscalerStrip key={view.shape} view={view} />
          ))}
        </div>
      )}

      {data && data.nodes.length === 0 && <EmptyState />}

      {data && data.nodes.length > 0 && (
        <div className="grid gap-4 grid-cols-[repeat(auto-fill,minmax(min(360px,100%),1fr))]">
          {[...data.nodes]
            .sort((a, b) => {
              // Booting shadows first, then AWS, then by earliest pipeline
              // stage, then hostname
              const bootCmp = (a.state === 'booting' ? 0 : 1) - (b.state === 'booting' ? 0 : 1)
              if (bootCmp !== 0) return bootCmp
              const envCmp = (a.deploy_env === 'aws' ? 0 : 1) - (b.deploy_env === 'aws' ? 0 : 1)
              if (envCmp !== 0) return envCmp
              const stageA = Math.min(...a.engines.map((e) => stageRank(e.stage)), 99)
              const stageB = Math.min(...b.engines.map((e) => stageRank(e.stage)), 99)
              if (stageA !== stageB) return stageA - stageB
              return a.hostname.localeCompare(b.hostname)
            })
            .map((node) =>
              node.state === 'booting' ? (
                <BootingNodeCard key={node.node_id} node={node} />
              ) : (
                <NodeCard key={node.node_id} node={node} />
              ),
            )}
        </div>
      )}
    </div>
  )
}
