import type { SettingValue } from '@/api/types'

/**
 * M95: how a nullable setting's field should read.
 *
 * "Unset" (no DB row) and "unknown effective value" (the external source
 * can't answer) are independent facts — a stale-snapshot autoscale
 * deployment and a lite deployment must not look the same. Keeping them
 * as distinct variants is what stops the UI showing a code default as if
 * it were the value actually in force.
 */
export type NullableFieldDisplay =
  | { kind: 'set'; value: unknown }
  | { kind: 'inherited-known'; effectiveValue: unknown }
  | { kind: 'inherited-unknown' }

export function deriveNullableDisplay(setting: SettingValue): NullableFieldDisplay {
  if (setting.is_overridden && setting.value !== null && setting.value !== undefined) {
    return { kind: 'set', value: setting.value }
  }
  if (setting.effective_known && setting.effective_value !== null && setting.effective_value !== undefined) {
    return { kind: 'inherited-known', effectiveValue: setting.effective_value }
  }
  return { kind: 'inherited-unknown' }
}
