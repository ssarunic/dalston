import { useState, useCallback, useEffect, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { HTTPError } from 'ky'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Dialog } from '@/components/ui/dialog'
import {
  useSettingsNamespaces,
  useSettingsNamespace,
  useUpdateSettings,
  useResetSettings,
} from '@/hooks/useSettings'
import type { SettingValue } from '@/api/types'
import {
  Gauge,
  Server,
  AudioLines,
  Clock,
  Monitor,
  RotateCcw,
  Copy,
  Check,
} from 'lucide-react'

const NAMESPACE_ICONS: Record<string, typeof Gauge> = {
  rate_limits: Gauge,
  engines: Server,
  audio: AudioLines,
  retention: Clock,
  system: Monitor,
}

// --------------------------------------------------------------------------
// Validation helpers
// --------------------------------------------------------------------------

/**
 * Validate a setting value against its constraints.
 * Returns an error message or null if valid.
 */
function validateSettingValue(setting: SettingValue, value: unknown): string | null {
  // Empty values are invalid for numeric fields
  if (value === '' || value === null || value === undefined) {
    if (setting.value_type === 'int' || setting.value_type === 'float') {
      return 'Value is required'
    }
    return null
  }

  if (setting.value_type === 'int') {
    if (typeof value !== 'number' || !Number.isInteger(value)) {
      return 'Must be a whole number'
    }
    if (setting.min_value != null && value < setting.min_value) {
      return `Minimum value is ${setting.min_value}`
    }
    if (setting.max_value != null && value > setting.max_value) {
      return `Maximum value is ${setting.max_value}`
    }
  }

  if (setting.value_type === 'float') {
    if (typeof value !== 'number' || isNaN(value)) {
      return 'Must be a number'
    }
    if (setting.min_value != null && value < setting.min_value) {
      return `Minimum value is ${setting.min_value}`
    }
    if (setting.max_value != null && value > setting.max_value) {
      return `Maximum value is ${setting.max_value}`
    }
  }

  if (setting.value_type === 'select') {
    if (setting.options && !setting.options.includes(String(value))) {
      return `Must be one of: ${setting.options.join(', ')}`
    }
  }

  return null
}

// --------------------------------------------------------------------------
// SettingField component
// --------------------------------------------------------------------------

function SettingField({
  setting,
  value,
  error,
  onChange,
}: {
  setting: SettingValue
  value: unknown
  error: string | null
  onChange: (key: string, value: unknown) => void
}) {
  const isOverridden = value !== setting.default_value
  const inputId = `setting-${setting.key}`
  const errorId = `${inputId}-error`
  const hasError = error !== null

  const baseInputClasses = "flex h-11 w-full sm:max-w-xs rounded-md border bg-background px-3 py-2 text-base sm:text-sm ring-offset-background placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-offset-2"
  const normalInputClasses = `${baseInputClasses} border-input focus:ring-ring`
  const errorInputClasses = `${baseInputClasses} border-red-500 focus:ring-red-500`

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <label htmlFor={inputId} className="text-sm font-medium text-foreground">
          {setting.label}
        </label>
        {isOverridden && (
          <span className="inline-block h-2 w-2 rounded-full bg-primary" title="Overridden" aria-label="Setting overridden" />
        )}
      </div>
      <p className="text-sm text-muted-foreground">{setting.description}</p>

      {setting.value_type === 'bool' ? (
        <div className="flex items-center gap-3 pt-1">
          <button
            id={inputId}
            type="button"
            role="switch"
            aria-checked={value === true}
            aria-label={`${setting.label}: ${value ? 'Enabled' : 'Disabled'}`}
            onClick={() => onChange(setting.key, !value)}
            className={`relative inline-flex h-7 w-12 items-center rounded-full transition-colors touch-manipulation ${
              value ? 'bg-primary' : 'bg-muted'
            }`}
          >
            <span
              className={`inline-block h-5 w-5 transform rounded-full bg-white transition-transform ${
                value ? 'translate-x-6' : 'translate-x-1'
              }`}
            />
          </button>
          <span className="text-sm text-muted-foreground">
            {value ? 'Enabled' : 'Disabled'}
          </span>
        </div>
      ) : setting.value_type === 'select' ? (
        <select
          id={inputId}
          value={String(value)}
          onChange={(e) => onChange(setting.key, e.target.value)}
          aria-invalid={hasError}
          aria-describedby={hasError ? errorId : undefined}
          className={hasError ? errorInputClasses : normalInputClasses}
        >
          {setting.options?.map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      ) : (
        <input
          id={inputId}
          type="number"
          inputMode="numeric"
          value={String(value ?? '')}
          onChange={(e) => {
            const raw = e.target.value
            if (raw === '') {
              onChange(setting.key, '')
              return
            }
            const parsed =
              setting.value_type === 'float' ? parseFloat(raw) : parseInt(raw, 10)
            if (!isNaN(parsed)) onChange(setting.key, parsed)
          }}
          min={setting.min_value ?? undefined}
          max={setting.max_value ?? undefined}
          step={setting.value_type === 'float' ? 'any' : '1'}
          aria-invalid={hasError}
          aria-describedby={hasError ? errorId : undefined}
          className={hasError ? errorInputClasses : normalInputClasses}
        />
      )}

      {hasError && (
        <p id={errorId} className="text-xs text-red-500" role="alert">
          {error}
        </p>
      )}

      <p className="text-xs text-muted-foreground">
        Default: {String(setting.default_value)}
        {setting.env_var && <span className="hidden sm:inline"> &middot; Env: {setting.env_var}</span>}
      </p>
    </div>
  )
}

// --------------------------------------------------------------------------
// SystemInfoTab
// --------------------------------------------------------------------------

function SystemInfoTab({ settings }: { settings: SettingValue[] }) {
  const [copiedKey, setCopiedKey] = useState<string | null>(null)

  const copyToClipboard = useCallback((key: string, value: string) => {
    navigator.clipboard.writeText(value)
    setCopiedKey(key)
    setTimeout(() => setCopiedKey(null), 2000)
  }, [])

  return (
    <Card className="overflow-hidden">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Monitor className="h-4 w-4" />
          System Information
        </CardTitle>
      </CardHeader>
      <CardContent className="overflow-hidden">
        <div className="rounded-md border border-blue-500/20 bg-blue-500/10 p-3 mb-4">
          <p className="text-sm text-blue-400">
            System settings are read-only and controlled by environment variables.
          </p>
        </div>
        <div className="divide-y divide-border overflow-hidden">
          {settings.map((setting) => (
            <div
              key={setting.key}
              className="flex flex-col sm:flex-row sm:items-center sm:justify-between py-3 gap-1 sm:gap-2 min-w-0"
            >
              <span className="text-sm text-muted-foreground sm:w-32 sm:shrink-0">
                {setting.label}
              </span>
              <div className="flex items-center gap-2 min-w-0 overflow-hidden sm:justify-end">
                <span className="text-sm font-mono truncate min-w-0 sm:text-right">
                  {String(setting.value)}
                </span>
                <button
                  type="button"
                  onClick={() => copyToClipboard(setting.key, String(setting.value))}
                  className="text-muted-foreground hover:text-foreground active:text-foreground shrink-0 p-2 -m-1 touch-manipulation"
                  title="Copy to clipboard"
                  aria-label={`Copy ${setting.label} value`}
                >
                  {copiedKey === setting.key ? (
                    <Check className="h-4 w-4 text-green-500" />
                  ) : (
                    <Copy className="h-4 w-4" />
                  )}
                </button>
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}

// --------------------------------------------------------------------------
// EditableNamespaceTab
// --------------------------------------------------------------------------

function EditableNamespaceTab({ namespace }: { namespace: string }) {
  const { data, isLoading } = useSettingsNamespace(namespace)
  const updateMutation = useUpdateSettings(namespace)
  const resetMutation = useResetSettings(namespace)

  // Local form state (dirty tracking)
  const [formValues, setFormValues] = useState<Record<string, unknown>>({})
  const [fieldErrors, setFieldErrors] = useState<Record<string, string | null>>({})
  const [isResetDialogOpen, setIsResetDialogOpen] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  // Sync form state when data loads - this is intentional to reset form when switching namespaces
  useEffect(() => {
    if (data?.settings) {
      const initial: Record<string, unknown> = {}
      for (const s of data.settings) {
        initial[s.key] = s.value
      }
      /* eslint-disable react-hooks/set-state-in-effect -- intentional: reset form state when namespace data changes */
      setFormValues(initial)
      setFieldErrors({})
      setErrorMessage(null)
      /* eslint-enable react-hooks/set-state-in-effect */
    }
  }, [data])

  const handleChange = useCallback((key: string, value: unknown) => {
    setFormValues((prev) => ({ ...prev, [key]: value }))
    // Validate the field on change
    const setting = data?.settings.find((s) => s.key === key)
    if (setting) {
      const error = validateSettingValue(setting, value)
      setFieldErrors((prev) => ({ ...prev, [key]: error }))
    }
    setErrorMessage(null)
  }, [data])

  // Compute dirty keys - memoized to avoid changing useCallback dependencies on every render
  const dirtyKeys = useMemo(
    () => data?.settings.filter((s) => formValues[s.key] !== s.value).map((s) => s.key) ?? [],
    [data?.settings, formValues]
  )
  const isDirty = dirtyKeys.length > 0

  const handleCancel = useCallback(() => {
    if (data?.settings) {
      const initial: Record<string, unknown> = {}
      for (const s of data.settings) {
        initial[s.key] = s.value
      }
      setFormValues(initial)
      setFieldErrors({})
      setErrorMessage(null)
    }
  }, [data])

  // Check if any field has validation errors
  const hasValidationErrors = Object.values(fieldErrors).some((error) => error !== null)

  const handleSave = useCallback(async () => {
    if (!data) return

    // Validate all dirty fields before saving
    const errors: Record<string, string | null> = {}
    let hasErrors = false
    for (const key of dirtyKeys) {
      const setting = data.settings.find((s) => s.key === key)
      if (setting) {
        const error = validateSettingValue(setting, formValues[key])
        errors[key] = error
        if (error) hasErrors = true
      }
    }
    setFieldErrors((prev) => ({ ...prev, ...errors }))

    if (hasErrors) {
      setErrorMessage('Please fix validation errors before saving')
      return
    }

    const updates: Record<string, unknown> = {}
    for (const key of dirtyKeys) {
      updates[key] = formValues[key]
    }
    try {
      await updateMutation.mutateAsync({
        settings: updates,
        expected_updated_at: data.updated_at,
      })
      setErrorMessage(null)
    } catch (err) {
      if (err instanceof HTTPError && err.response.status === 409) {
        setErrorMessage('Settings were modified by another admin. Please refresh and try again.')
      } else if (err instanceof HTTPError && err.response.status === 400) {
        const body = await err.response.json()
        setErrorMessage(body.detail || 'Validation error')
      } else {
        setErrorMessage('Failed to save settings')
      }
    }
  }, [data, dirtyKeys, formValues, updateMutation])

  const handleReset = useCallback(async () => {
    try {
      await resetMutation.mutateAsync()
      setIsResetDialogOpen(false)
      setErrorMessage(null)
    } catch {
      setErrorMessage('Failed to reset settings')
    }
  }, [resetMutation])

  if (isLoading) {
    return (
      <Card>
        <CardContent className="pt-6 space-y-6">
          {[1, 2, 3].map((i) => (
            <div key={i} className="space-y-2">
              <Skeleton className="h-4 w-40" />
              <Skeleton className="h-3 w-64" />
              <Skeleton className="h-10 w-64" />
            </div>
          ))}
        </CardContent>
      </Card>
    )
  }

  if (!data) return null

  const Icon = NAMESPACE_ICONS[namespace] ?? Gauge
  const hasOverrides = data.settings.some((s) => s.is_overridden)

  return (
    <>
      <Card>
        <CardHeader>
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2">
            <CardTitle className="flex items-center gap-2 text-base">
              <Icon className="h-4 w-4" />
              {data.label}
            </CardTitle>
            {hasOverrides && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setIsResetDialogOpen(true)}
                className="text-muted-foreground h-9 px-3 self-start sm:self-auto touch-manipulation"
              >
                <RotateCcw className="h-4 w-4 mr-1.5" />
                Reset to defaults
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent>
          <div className="divide-y divide-border">
            {data.settings.map((setting) => (
              <div key={setting.key} className="py-4 first:pt-0 last:pb-0">
                <SettingField
                  setting={setting}
                  value={formValues[setting.key] ?? setting.value}
                  error={fieldErrors[setting.key] ?? null}
                  onChange={handleChange}
                />
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Sticky save bar */}
      {isDirty && (
        <div className={`sticky bottom-0 mt-4 flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 sm:gap-2 rounded-lg border bg-card p-4 ${hasValidationErrors ? 'border-red-500/50' : 'border-border'}`}>
          <span className="flex items-center gap-2 text-sm text-muted-foreground">
            {hasValidationErrors ? (
              <>
                <span className="inline-block h-2 w-2 rounded-full bg-red-500" aria-hidden="true" />
                <span className="text-red-400">Fix errors before saving</span>
              </>
            ) : (
              <>
                <span className="inline-block h-2 w-2 rounded-full bg-amber-500" aria-hidden="true" />
                {dirtyKeys.length} unsaved {dirtyKeys.length === 1 ? 'change' : 'changes'}
              </>
            )}
          </span>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="default" className="flex-1 sm:flex-initial h-11 sm:h-9" onClick={handleCancel}>
              Cancel
            </Button>
            <Button
              size="default"
              className="flex-1 sm:flex-initial h-11 sm:h-9"
              onClick={handleSave}
              disabled={updateMutation.isPending || hasValidationErrors}
            >
              {updateMutation.isPending ? 'Saving...' : 'Save'}
            </Button>
          </div>
        </div>
      )}

      {/* Error message */}
      {errorMessage && (
        <div className="mt-2 rounded-md border border-red-500/20 bg-red-500/10 p-3">
          <p className="text-sm text-red-400">{errorMessage}</p>
        </div>
      )}

      {/* Success message */}
      {updateMutation.isSuccess && !isDirty && (
        <div className="mt-2 rounded-md border border-green-500/20 bg-green-500/10 p-3">
          <p className="text-sm text-green-400">Settings saved successfully.</p>
        </div>
      )}

      {/* Reset confirmation dialog */}
      <Dialog open={isResetDialogOpen} onOpenChange={setIsResetDialogOpen}>
        <Card className="w-full max-w-md mx-4">
          <CardHeader>
            <CardTitle className="text-lg">Reset {data.label}</CardTitle>
            <p className="text-sm text-muted-foreground">
              This will revert all settings in this section to their default values.
            </p>
          </CardHeader>
          <CardContent className="space-y-4">
            {data.settings.filter((s) => s.is_overridden).length > 0 && (
              <div className="rounded-md bg-muted p-3 text-xs sm:text-sm font-mono space-y-1 overflow-x-auto">
                {data.settings
                  .filter((s) => s.is_overridden)
                  .map((s) => (
                    <div key={s.key} className="whitespace-nowrap">
                      {s.key}: {String(s.value)} &rarr; {String(s.default_value)}
                    </div>
                  ))}
              </div>
            )}
            <div className="flex flex-col-reverse sm:flex-row sm:justify-end gap-2">
              <Button
                variant="outline"
                className="h-11 sm:h-9"
                onClick={() => setIsResetDialogOpen(false)}
              >
                Cancel
              </Button>
              <Button
                variant="destructive"
                className="h-11 sm:h-9"
                onClick={handleReset}
                disabled={resetMutation.isPending}
              >
                {resetMutation.isPending ? 'Resetting...' : 'Reset to defaults'}
              </Button>
            </div>
          </CardContent>
        </Card>
      </Dialog>
    </>
  )
}

// --------------------------------------------------------------------------
// Settings page
// --------------------------------------------------------------------------

export function Settings() {
  const [searchParams, setSearchParams] = useSearchParams()
  const activeTab = searchParams.get('tab') || 'rate_limits'

  const { data: namespacesData, isLoading: namespacesLoading } = useSettingsNamespaces()
  const namespaces = namespacesData?.namespaces ?? []

  const handleTabChange = useCallback(
    (ns: string) => {
      setSearchParams({ tab: ns })
    },
    [setSearchParams],
  )

  return (
    <div className="space-y-6 min-w-0">
      {/* Page header */}
      <div>
        <h1 className="text-2xl font-bold">Settings</h1>
        <p className="text-muted-foreground">
          Manage system configuration and operational parameters
        </p>
      </div>

      {/* Mobile: Dropdown selector */}
      <div className="sm:hidden">
        {namespacesLoading ? (
          <Skeleton className="h-11 w-full" />
        ) : (
          <select
            value={activeTab}
            onChange={(e) => handleTabChange(e.target.value)}
            aria-label="Settings section"
            className="flex h-11 w-full rounded-md border border-input bg-background px-3 py-2 text-base ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
          >
            {namespaces.map((ns) => (
              <option key={ns.namespace} value={ns.namespace}>
                {ns.label}{ns.has_overrides ? ' •' : ''}
              </option>
            ))}
          </select>
        )}
      </div>

      {/* Desktop: Horizontal tab bar */}
      <nav className="hidden sm:block" aria-label="Settings sections">
        <div className="flex gap-1 border-b border-border">
          {namespacesLoading
            ? [1, 2, 3, 4, 5, 6].map((i) => (
                <Skeleton key={i} className="h-10 w-24" />
              ))
            : namespaces.map((ns) => {
                const Icon = NAMESPACE_ICONS[ns.namespace] ?? Gauge
                const isActive = activeTab === ns.namespace
                return (
                  <button
                    key={ns.namespace}
                    type="button"
                    role="tab"
                    aria-selected={isActive}
                    aria-controls={`panel-${ns.namespace}`}
                    onClick={() => handleTabChange(ns.namespace)}
                    className={`flex items-center gap-1.5 px-4 py-2 text-sm whitespace-nowrap border-b-2 transition-colors ${
                      isActive
                        ? 'border-primary text-foreground'
                        : 'border-transparent text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    <Icon className="h-4 w-4" aria-hidden="true" />
                    {ns.label}
                    {ns.has_overrides && (
                      <span className="inline-block h-1.5 w-1.5 rounded-full bg-primary" aria-label="Has overrides" />
                    )}
                  </button>
                )
              })}
        </div>
      </nav>

      {/* Tab content */}
      {activeTab === 'system' ? (
        <SystemInfoTabWrapper />
      ) : (
        <EditableNamespaceTab key={activeTab} namespace={activeTab} />
      )}
    </div>
  )
}

function SystemInfoTabWrapper() {
  const { data, isLoading } = useSettingsNamespace('system')

  if (isLoading) {
    return (
      <Card>
        <CardContent className="pt-6 space-y-4">
          {[1, 2, 3, 4, 5].map((i) => (
            <Skeleton key={i} className="h-6 w-full" />
          ))}
        </CardContent>
      </Card>
    )
  }

  if (!data) return null

  return <SystemInfoTab settings={data.settings} />
}
