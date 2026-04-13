import { useMemo } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Activity, AlertTriangle, Gauge, Timer } from 'lucide-react'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { useQueueBoard } from '@/hooks/useQueueBoard'
import { STAGE_LABELS } from '@/lib/stages'
import { PivotBoard } from '@/components/QueueBoard/PivotBoard'
import {
  ViewPicker,
  type BoardView,
} from '@/components/QueueBoard/ViewPicker'
import type {
  BoardColumnGrouping,
  BoardRowGrouping,
} from '@/components/QueueBoard/PivotBoard'

/**
 * Map each named view to the underlying (groupByColumn, groupByRow)
 * pair consumed by PivotBoard. Adding a fourth view is a one-line
 * change here.
 */
const VIEWS: Record<
  BoardView,
  { column: BoardColumnGrouping; row: BoardRowGrouping }
> = {
  grid: { column: 'stage', row: 'job' },
  'stage-board': { column: 'stage', row: 'none' },
  'job-strips': { column: 'none', row: 'job' },
}

const VALID_VIEWS: readonly BoardView[] = ['grid', 'stage-board', 'job-strips']

function parseView(raw: string | null): BoardView {
  if (raw && (VALID_VIEWS as readonly string[]).includes(raw)) {
    return raw as BoardView
  }
  return 'grid'
}

function formatMs(ms: number | null | undefined): string {
  if (ms == null) return '—'
  if (ms < 1000) return `${Math.round(ms)}ms`
  const secs = ms / 1000
  if (secs < 60) return `${secs.toFixed(1)}s`
  const mins = Math.floor(secs / 60)
  return `${mins}m ${Math.round(secs % 60)}s`
}

function SummaryCard({
  title,
  value,
  subtitle,
  icon: Icon,
  accent,
}: {
  title: string
  value: string | number
  subtitle?: string
  icon: React.ElementType
  accent?: 'default' | 'warning'
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <span className="text-sm font-medium text-muted-foreground">{title}</span>
        <Icon
          className={
            accent === 'warning'
              ? 'h-4 w-4 text-amber-400'
              : 'h-4 w-4 text-muted-foreground'
          }
        />
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">{value}</div>
        {subtitle && (
          <p className="text-xs text-muted-foreground">{subtitle}</p>
        )}
      </CardContent>
    </Card>
  )
}

export function QueueBoard() {
  const [searchParams, setSearchParams] = useSearchParams()
  const view = parseView(searchParams.get('view'))
  const { column: groupByColumn, row: groupByRow } = VIEWS[view]

  const handleViewChange = (next: BoardView) => {
    const params = new URLSearchParams(searchParams)
    if (next === 'grid') {
      params.delete('view')
    } else {
      params.set('view', next)
    }
    setSearchParams(params, { replace: true })
  }

  const { data, isLoading, error } = useQueueBoard()

  // Find the current bottleneck stage for the summary card
  const bottleneckName = useMemo(() => {
    if (!data?.stages?.length) return null
    let winner: { stage: string; depth: number } | null = null
    for (const s of data.stages) {
      if (!winner || s.queue_depth > winner.depth) {
        winner = { stage: s.stage, depth: s.queue_depth }
      }
    }
    if (!winner || winner.depth === 0) return null
    return {
      name: STAGE_LABELS[winner.stage]?.label ?? winner.stage,
      depth: winner.depth,
    }
  }, [data])

  const activeJobCount = data?.jobs.length ?? 0
  const hasActiveJobs = activeJobCount > 0

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold">Queue Board</h1>
          <p className="text-muted-foreground">
            Live cross-job view of every active task flowing through the pipeline.
          </p>
        </div>
        <ViewPicker value={view} onChange={handleViewChange} />
      </div>

      {/* Summary cards */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <SummaryCard
          title="Active Jobs"
          value={isLoading && !data ? '-' : activeJobCount}
          subtitle={
            hasActiveJobs
              ? `${data?.tasks.length ?? 0} tasks in flight`
              : 'No jobs in flight'
          }
          icon={Activity}
        />
        <SummaryCard
          title="Bottleneck"
          value={bottleneckName ? bottleneckName.name : 'None'}
          subtitle={
            bottleneckName
              ? `${bottleneckName.depth} queued`
              : 'All stages clear'
          }
          icon={AlertTriangle}
          accent={bottleneckName ? 'warning' : 'default'}
        />
        <SummaryCard
          title="Completed (1h)"
          value={data?.completed_last_hour ?? '-'}
          subtitle="Jobs finished in last hour"
          icon={Gauge}
        />
        <SummaryCard
          title="Avg Pipeline"
          value={formatMs(data?.avg_pipeline_ms ?? null)}
          subtitle="Wall-clock per job (1h)"
          icon={Timer}
        />
      </div>

      {/* Error banner */}
      {error && (
        <div className="rounded-md border border-destructive/60 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          Failed to load queue board: {error.message}
        </div>
      )}

      {/* Board */}
      {!data && isLoading && (
        <div className="rounded-lg border border-border bg-card px-4 py-10 text-center text-sm text-muted-foreground">
          Loading queue board…
        </div>
      )}

      {data && !hasActiveJobs && (
        <div className="rounded-lg border border-border bg-card px-4 py-12 text-center">
          <p className="text-base font-medium text-foreground">
            All clear — no active jobs
          </p>
          <p className="mt-1 text-sm text-muted-foreground">
            The queue board lights up whenever batch jobs are in flight.
          </p>
          <Link
            to="/jobs/new"
            className="mt-4 inline-block rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:brightness-110"
          >
            Submit a new job
          </Link>
        </div>
      )}

      {data && hasActiveJobs && (
        <PivotBoard
          tasks={data.tasks}
          jobs={data.jobs}
          visibleStages={data.visible_stages}
          stageHealth={data.stages}
          groupByColumn={groupByColumn}
          groupByRow={groupByRow}
        />
      )}

      {/* Hidden-stages hint */}
      {data && data.hidden_stages.length > 0 && (
        <p className="text-xs text-muted-foreground">
          <span className="font-medium">Hidden: </span>
          {data.hidden_stages
            .map((stage) => STAGE_LABELS[stage]?.label ?? stage)
            .join(', ')}
          {' — no active jobs use these stages'}
        </p>
      )}
    </div>
  )
}
