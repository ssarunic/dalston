import type { AutoscalerShapeView } from '@/api/types'

/**
 * M95: the autoscaler strip's mutually-exclusive display states, derived
 * from one shape's tick snapshot. Pure and unit-tested — the checkpoint
 * requires every state to be reachable, which is verifiable here without
 * rendering.
 *
 * Priority: stale > critical > warning > cooldown > idle > nominal.
 */
export type StripState =
  | { kind: 'stale'; lastTickAt: string | null }
  | {
      kind: 'critical'
      blockedKind: string
      earliestWaitDeadlineAt: string | null
      blockedSince: string
      maxBacklog: number
    }
  | { kind: 'warning'; blockedKind: string; maxBacklog: number }
  | { kind: 'cooldown'; idleSinceS: number; scaleDownAfterS: number | null; live: number; desired: number }
  | { kind: 'idle'; maxInstances: number | null }
  | { kind: 'nominal' }

export function deriveStripState(shape: AutoscalerShapeView): StripState {
  if (shape.stale) {
    return { kind: 'stale', lastTickAt: shape.last_tick_at }
  }
  const live = shape.live ?? 0
  const pending = shape.pending ?? 0
  const maxBacklog = shape.max_backlog ?? 0
  if (shape.blocked) {
    if (live === 0 && maxBacklog > 0) {
      return {
        kind: 'critical',
        blockedKind: shape.blocked.kind,
        earliestWaitDeadlineAt: shape.earliest_wait_deadline_at,
        blockedSince: shape.blocked.since,
        maxBacklog,
      }
    }
    return { kind: 'warning', blockedKind: shape.blocked.kind, maxBacklog }
  }
  if (maxBacklog === 0 && live > 0) {
    return {
      kind: 'cooldown',
      idleSinceS: shape.idle_since_s ?? 0,
      scaleDownAfterS: shape.policy?.scale_down_after_s ?? null,
      live,
      desired: shape.desired ?? 0,
    }
  }
  if (maxBacklog === 0 && live === 0 && pending === 0) {
    return { kind: 'idle', maxInstances: shape.policy?.max_instances ?? null }
  }
  return { kind: 'nominal' }
}
