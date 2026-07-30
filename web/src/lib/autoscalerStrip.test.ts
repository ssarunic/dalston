import { describe, expect, it } from 'vitest'
import { deriveStripState } from './autoscalerStrip'
import type { AutoscalerShapeView } from '@/api/types'

function makeView(overrides: Partial<AutoscalerShapeView> = {}): AutoscalerShapeView {
  return {
    shape: 'nemo+pyannote',
    stale: false,
    last_tick_at: '2026-07-30T12:04:00+00:00',
    ts: '2026-07-30T12:04:00+00:00',
    action: 'none',
    applied: 'at desired capacity',
    desired: 2,
    live: 2,
    pending: 0,
    spot_live: 2,
    on_demand_live: 0,
    max_backlog: 41,
    per_engine: { nemo: { lag: 41, in_flight: 0 } },
    idle_since_s: null,
    policy: {
      tasks_per_instance: 20,
      min_instances: 0,
      max_instances: 5,
      scale_down_after_s: 2100,
      overrides_applied: [],
      override_error: null,
    },
    blocked: null,
    earliest_wait_deadline_at: null,
    ...overrides,
  }
}

const blocked = {
  kind: 'spot_quota',
  since: '2026-07-30T11:58:00+00:00',
  consecutive_ticks: 6,
  detail: 'MaxSpotInstanceCountExceeded',
}

describe('deriveStripState', () => {
  it('stale wins over everything else', () => {
    const state = deriveStripState(makeView({ stale: true, blocked, live: 0 }))
    expect(state).toEqual({ kind: 'stale', lastTickAt: '2026-07-30T12:04:00+00:00' })
  })

  it('critical: blocked with zero live instances and backlog', () => {
    const state = deriveStripState(
      makeView({
        blocked,
        live: 0,
        max_backlog: 41,
        earliest_wait_deadline_at: '2026-07-30T12:22:00+00:00',
      }),
    )
    expect(state).toMatchObject({
      kind: 'critical',
      blockedKind: 'spot_quota',
      earliestWaitDeadlineAt: '2026-07-30T12:22:00+00:00',
      maxBacklog: 41,
    })
  })

  it('critical without a task deadline keeps the blocked-since fallback', () => {
    const state = deriveStripState(makeView({ blocked, live: 0, max_backlog: 41 }))
    expect(state).toMatchObject({
      kind: 'critical',
      earliestWaitDeadlineAt: null,
      blockedSince: blocked.since,
    })
  })

  it('warning: blocked but capacity still serving', () => {
    const state = deriveStripState(makeView({ blocked, live: 1, max_backlog: 41 }))
    expect(state).toEqual({ kind: 'warning', blockedKind: 'spot_quota', maxBacklog: 41 })
  })

  it('cooldown: idle fleet with live instances', () => {
    const state = deriveStripState(
      makeView({ max_backlog: 0, live: 1, desired: 0, idle_since_s: 1380 }),
    )
    expect(state).toEqual({
      kind: 'cooldown',
      idleSinceS: 1380,
      scaleDownAfterS: 2100,
      live: 1,
      desired: 0,
    })
  })

  it('cooldown on the first idle tick before idle_since exists', () => {
    const state = deriveStripState(
      makeView({ max_backlog: 0, live: 1, desired: 0, idle_since_s: null }),
    )
    expect(state).toMatchObject({ kind: 'cooldown', idleSinceS: 0 })
  })

  it('warmFloor: idle at an always-on minimum is not a cooldown', () => {
    // min_instances=1 keeps live==desired==1 forever; that worker is never
    // terminated, so "cooldown running" would be a promise that never lands.
    const state = deriveStripState(
      makeView({ max_backlog: 0, live: 1, desired: 1, idle_since_s: 99999 }),
    )
    expect(state).toEqual({ kind: 'warmFloor', live: 1 })
  })

  it('cooldown only once there is surplus above desired', () => {
    const state = deriveStripState(
      makeView({ max_backlog: 0, live: 2, desired: 1, idle_since_s: 60 }),
    )
    expect(state).toMatchObject({ kind: 'cooldown', live: 2, desired: 1 })
  })

  it('idle: scaled to zero', () => {
    const state = deriveStripState(
      makeView({ max_backlog: 0, live: 0, pending: 0, desired: 0 }),
    )
    expect(state).toEqual({ kind: 'idle', maxInstances: 5 })
  })

  it('idle survives a null policy echo', () => {
    const state = deriveStripState(
      makeView({ max_backlog: 0, live: 0, pending: 0, desired: 0, policy: null }),
    )
    expect(state).toEqual({ kind: 'idle', maxInstances: null })
  })

  it('nominal: backlog with capacity serving', () => {
    expect(deriveStripState(makeView())).toEqual({ kind: 'nominal' })
  })

  it('nominal: zero backlog but a worker still booting', () => {
    const state = deriveStripState(
      makeView({ max_backlog: 0, live: 0, pending: 1, desired: 1 }),
    )
    expect(state).toEqual({ kind: 'nominal' })
  })

  it('on-demand workers do not change the state (orthogonal badge)', () => {
    const state = deriveStripState(makeView({ on_demand_live: 1, spot_live: 1 }))
    expect(state).toEqual({ kind: 'nominal' })
  })
})
