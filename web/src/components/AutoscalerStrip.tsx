import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { useNowMs } from '@/hooks/useNowMs'
import { formatDurationShort, formatElapsed } from '@/lib/time'
import { deriveStripState, type StripState } from '@/lib/autoscalerStrip'
import type { AutoscalerShapeView } from '@/api/types'
import { cn } from '@/lib/utils'
import { S } from '@/lib/strings'

// M95: slot row — filled green = live, pulsing amber = booting, dashed
// gray = unlaunched headroom up to max_instances. Slots instead of ghost
// node cards: at scale-to-zero idle a page of empty gray cards reads as
// breakage, not readiness.
function SlotRow({ live, booting, max }: { live: number; booting: number; max: number }) {
  const total = Math.max(max, live + booting)
  const slots = Array.from({ length: total }, (_, i) => {
    if (i < live) return 'live'
    if (i < live + booting) return 'booting'
    return 'empty'
  })
  return (
    <div className="flex items-center gap-1.5" aria-label={`${live} live, ${booting} booting, max ${max}`}>
      {slots.map((slot, i) => (
        <span
          key={i}
          className={cn(
            'inline-block h-3 w-3 rounded-full',
            slot === 'live' && 'bg-green-500',
            slot === 'booting' && 'bg-amber-500 animate-pulse',
            slot === 'empty' && 'border border-dashed border-muted-foreground/40',
          )}
        />
      ))}
    </div>
  )
}

function FormulaLine({ view }: { view: AutoscalerShapeView }) {
  const perInstance = view.policy?.tasks_per_instance
  if (view.max_backlog === null || !perInstance || view.desired === null) return null
  return (
    <p className="text-sm text-muted-foreground">
      {S.autoscalerStrip.formula(view.max_backlog, perInstance, view.desired)}
    </p>
  )
}

function StatusLine({ view, state, nowMs }: { view: AutoscalerShapeView; state: StripState; nowMs: number | null }) {
  switch (state.kind) {
    case 'stale':
      return (
        <p className="text-sm text-muted-foreground">
          {state.lastTickAt && nowMs !== null
            ? S.autoscalerStrip.staleSince(formatElapsed(state.lastTickAt, nowMs) + ' ago')
            : S.autoscalerStrip.stale}
        </p>
      )
    case 'critical': {
      const deadlineS =
        state.earliestWaitDeadlineAt && nowMs !== null
          ? (Date.parse(state.earliestWaitDeadlineAt) - nowMs) / 1000
          : null
      return (
        <p className="text-sm text-red-400">
          {S.autoscalerStrip.spotQuota(state.maxBacklog)}{' '}
          {deadlineS !== null
            ? S.autoscalerStrip.criticalDeadline(formatDurationShort(deadlineS))
            : nowMs !== null
              ? S.autoscalerStrip.criticalBlockedFor(formatElapsed(state.blockedSince, nowMs))
              : null}
        </p>
      )
    }
    case 'warning':
      return (
        <p className="text-sm text-amber-500">
          {state.blockedKind === 'spot_capacity'
            ? S.autoscalerStrip.spotCapacity(state.maxBacklog)
            : S.autoscalerStrip.spotQuota(state.maxBacklog)}
        </p>
      )
    case 'cooldown': {
      const surplus = Math.max(state.live - state.desired, 0)
      if (state.scaleDownAfterS !== null && surplus > 0) {
        const remaining = state.scaleDownAfterS - state.idleSinceS
        return (
          <p className="text-sm text-muted-foreground">
            {S.autoscalerStrip.cooldownTerminating(surplus, formatDurationShort(remaining))}
          </p>
        )
      }
      return <p className="text-sm text-muted-foreground">{S.autoscalerStrip.cooldownStarting}</p>
    }
    case 'idle':
      return (
        <p className="text-sm text-muted-foreground">{S.autoscalerStrip.idle(state.maxInstances)}</p>
      )
    case 'nominal':
      return view.applied ? (
        <p className="text-sm text-muted-foreground">{view.applied}</p>
      ) : null
  }
}

export function AutoscalerStrip({ view }: { view: AutoscalerShapeView }) {
  const nowMs = useNowMs()
  const state = deriveStripState(view)
  const live = view.live ?? 0
  const booting = view.pending ?? 0
  const max = view.policy?.max_instances ?? 0
  const onDemand = view.on_demand_live ?? 0

  return (
    <Card
      className={cn(
        state.kind === 'critical' && 'border-red-500/50',
        state.kind === 'warning' && 'border-amber-500/40',
        state.kind === 'stale' && 'border-muted-foreground/30 border-dashed',
      )}
    >
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-3">
          <CardTitle className="text-base font-medium">
            {S.autoscalerStrip.title(view.shape)}
          </CardTitle>
          {state.kind !== 'stale' && max > 0 && (
            <div className="flex items-center gap-3">
              <SlotRow live={live} booting={booting} max={max} />
              <span className="text-xs text-muted-foreground tabular-nums">
                {S.autoscalerStrip.fleet(live, booting, max)}
              </span>
            </div>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-1">
        {state.kind === 'nominal' && <FormulaLine view={view} />}
        {(state.kind === 'warning' || state.kind === 'critical') && <FormulaLine view={view} />}
        <StatusLine view={view} state={state} nowMs={nowMs} />
        {onDemand > 0 && state.kind !== 'stale' && (
          <p className="text-xs text-amber-500">{S.autoscalerStrip.onDemand(onDemand)}</p>
        )}
      </CardContent>
    </Card>
  )
}
