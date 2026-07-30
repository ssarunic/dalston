import { describe, expect, it } from 'vitest'
import { deriveNullableDisplay } from './settingsDisplay'
import type { SettingValue } from '@/api/types'

function makeSetting(overrides: Partial<SettingValue> = {}): SettingValue {
  return {
    key: 'max_instances',
    label: 'Maximum instances',
    description: 'Hard cap on simultaneous GPU instances per shape',
    value_type: 'int',
    value: null,
    default_value: null,
    is_overridden: false,
    env_var: '',
    nullable: true,
    effective_value: 3,
    effective_known: true,
    override_error: null,
    ...overrides,
  }
}

describe('deriveNullableDisplay', () => {
  it('explicit override reads as set', () => {
    const d = deriveNullableDisplay(makeSetting({ is_overridden: true, value: 5 }))
    expect(d).toEqual({ kind: 'set', value: 5 })
  })

  it('unset shows the control-plane value, not a code default', () => {
    const d = deriveNullableDisplay(makeSetting())
    expect(d).toEqual({ kind: 'inherited-known', effectiveValue: 3 })
  })

  it('unset with a silent autoscaler is unknown, never a guess', () => {
    const d = deriveNullableDisplay(
      makeSetting({ effective_known: false, effective_value: null }),
    )
    expect(d).toEqual({ kind: 'inherited-unknown' })
  })

  it('min_instances=0 is a real override, not treated as blank', () => {
    const d = deriveNullableDisplay(
      makeSetting({ key: 'min_instances', is_overridden: true, value: 0 }),
    )
    expect(d).toEqual({ kind: 'set', value: 0 })
  })

  it('inherited effective value of 0 stays known', () => {
    const d = deriveNullableDisplay(
      makeSetting({ key: 'min_instances', effective_value: 0 }),
    )
    expect(d).toEqual({ kind: 'inherited-known', effectiveValue: 0 })
  })
})
