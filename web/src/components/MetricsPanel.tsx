import { ExternalLink, TrendingUp, Clock, BarChart3, Mic } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import type { MetricsResponse, ThroughputBucket, EngineMetric } from '@/api/types'

function formatDuration(ms: number | null): string {
  if (ms === null) return '-'
  if (ms < 1000) return `${Math.round(ms)}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function formatRate(rate: number): string {
  return `${(rate * 100).toFixed(1)}%`
}

function formatHour(iso: string): string {
  const d = new Date(iso)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })
}

function ThroughputChart({ buckets }: { buckets: ThroughputBucket[] }) {
  const maxVal = Math.max(1, ...buckets.map((b) => b.completed + b.failed))

  return (
    <div className="space-y-2">
      <div className="flex items-end gap-px h-24">
        {buckets.map((bucket) => {
          const total = bucket.completed + bucket.failed
          const completedH = total > 0 ? (bucket.completed / maxVal) * 100 : 0
          const failedH = total > 0 ? (bucket.failed / maxVal) * 100 : 0

          return (
            <div
              key={bucket.hour}
              className="flex-1 flex flex-col justify-end group relative"
              title={`${formatHour(bucket.hour)}: ${bucket.completed} completed, ${bucket.failed} failed`}
            >
              {/* Tooltip */}
              <div className="absolute -top-8 left-1/2 -translate-x-1/2 bg-popover text-popover-foreground border border-border rounded px-1.5 py-0.5 text-[10px] whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-10">
                {bucket.completed + bucket.failed}
              </div>
              {failedH > 0 && (
                <div
                  className="bg-destructive/80 rounded-t-sm min-h-[1px]"
                  style={{ height: `${failedH}%` }}
                />
              )}
              {completedH > 0 && (
                <div
                  className={`bg-primary/70 min-h-[1px] ${failedH > 0 ? '' : 'rounded-t-sm'}`}
                  style={{ height: `${completedH}%` }}
                />
              )}
              {total === 0 && (
                <div className="bg-muted/40 rounded-t-sm min-h-[2px]" style={{ height: '2%' }} />
              )}
            </div>
          )
        })}
      </div>
      <div className="flex justify-between text-[10px] text-muted-foreground">
        <span>{buckets.length > 0 ? formatHour(buckets[0].hour) : ''}</span>
        <span>Now</span>
      </div>
    </div>
  )
}

function EngineTable({ engines }: { engines: EngineMetric[] }) {
  if (engines.length === 0) {
    return (
      <p className="text-sm text-muted-foreground text-center py-2">No engines registered</p>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border text-left">
            <th className="pb-2 font-medium text-muted-foreground">Engine</th>
            <th className="pb-2 font-medium text-muted-foreground text-right">Done</th>
            <th className="pb-2 font-medium text-muted-foreground text-right">Fail</th>
            <th className="pb-2 font-medium text-muted-foreground text-right">Avg</th>
            <th className="pb-2 font-medium text-muted-foreground text-right">P95</th>
            <th className="pb-2 font-medium text-muted-foreground text-right">Queue</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border">
          {engines.map((e) => (
            <tr key={e.engine_id} className="hover:bg-accent/50 transition-colors">
              <td className="py-1.5">
                <span className="font-mono text-xs">{e.engine_id}</span>
                <span className="text-muted-foreground text-xs ml-1.5">({e.stage})</span>
              </td>
              <td className="py-1.5 text-right tabular-nums">{e.completed}</td>
              <td className="py-1.5 text-right tabular-nums">
                {e.failed > 0 ? (
                  <span className="text-destructive">{e.failed}</span>
                ) : (
                  <span className="text-muted-foreground">0</span>
                )}
              </td>
              <td className="py-1.5 text-right tabular-nums text-muted-foreground">
                {formatDuration(e.avg_duration_ms)}
              </td>
              <td className="py-1.5 text-right tabular-nums text-muted-foreground">
                {formatDuration(e.p95_duration_ms)}
              </td>
              <td className="py-1.5 text-right tabular-nums">
                {e.queue_depth > 0 ? (
                  <span className="text-amber-500 font-medium">{e.queue_depth}</span>
                ) : (
                  <span className="text-muted-foreground">0</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function MetricsPanel({
  metrics,
  isLoading,
}: {
  metrics: MetricsResponse | undefined
  isLoading: boolean
}) {
  if (isLoading) {
    return (
      <div className="space-y-4">
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          {[...Array(4)].map((_, i) => (
            <Card key={i}>
              <CardContent className="p-6">
                <Skeleton className="h-4 w-24 mb-2" />
                <Skeleton className="h-8 w-16" />
              </CardContent>
            </Card>
          ))}
        </div>
        <Card>
          <CardContent className="p-6">
            <Skeleton className="h-24 w-full" />
          </CardContent>
        </Card>
      </div>
    )
  }

  if (!metrics) return null

  const rate1h = metrics.success_rates.find((r) => r.window === '1h')
  const rate24h = metrics.success_rates.find((r) => r.window === '24h')
  const totalThroughput24h = metrics.throughput.reduce(
    (sum, b) => sum + b.completed + b.failed,
    0,
  )

  return (
    <div className="space-y-4">
      {/* Summary metric cards */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <span className="text-sm font-medium text-muted-foreground">
              Jobs (24h)
            </span>
            <BarChart3 className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{totalThroughput24h.toLocaleString()}</div>
            <p className="text-xs text-muted-foreground">
              {rate24h ? `${rate24h.failed} failed` : 'no data'}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <span className="text-sm font-medium text-muted-foreground">
              Success Rate (1h)
            </span>
            <TrendingUp className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {rate1h ? formatRate(rate1h.rate) : '-'}
            </div>
            <p className="text-xs text-muted-foreground">
              {rate1h && rate1h.total > 0
                ? `${rate1h.completed}/${rate1h.total} jobs`
                : 'no jobs in window'}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <span className="text-sm font-medium text-muted-foreground">
              Audio Processed
            </span>
            <Mic className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {metrics.total_audio_minutes >= 60
                ? `${(metrics.total_audio_minutes / 60).toFixed(1)}h`
                : `${metrics.total_audio_minutes.toFixed(1)}m`}
            </div>
            <p className="text-xs text-muted-foreground">
              {metrics.total_jobs_all_time.toLocaleString()} jobs all time
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <span className="text-sm font-medium text-muted-foreground">
              Success Rate (24h)
            </span>
            <Clock className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {rate24h ? formatRate(rate24h.rate) : '-'}
            </div>
            <p className="text-xs text-muted-foreground">
              {rate24h && rate24h.total > 0
                ? `${rate24h.completed}/${rate24h.total} jobs`
                : 'no jobs in window'}
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Throughput chart + Engine table */}
      <div className="grid gap-4 grid-cols-1 lg:grid-cols-2">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle>Job Throughput (24h)</CardTitle>
            <div className="flex items-center gap-3 text-xs text-muted-foreground">
              <span className="flex items-center gap-1">
                <span className="inline-block w-2 h-2 rounded-sm bg-primary/70" />
                Completed
              </span>
              <span className="flex items-center gap-1">
                <span className="inline-block w-2 h-2 rounded-sm bg-destructive/80" />
                Failed
              </span>
            </div>
          </CardHeader>
          <CardContent>
            <ThroughputChart buckets={metrics.throughput} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between">
            <CardTitle>Engine Performance (24h)</CardTitle>
            {metrics.grafana_url && (
              <a
                href={metrics.grafana_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs text-primary hover:underline flex items-center gap-1"
              >
                Open Grafana
                <ExternalLink className="h-3 w-3" />
              </a>
            )}
          </CardHeader>
          <CardContent>
            <EngineTable engines={metrics.engines} />
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
