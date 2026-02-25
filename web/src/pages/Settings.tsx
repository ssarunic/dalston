import { useState, useCallback, useEffect } from 'react'
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
  Webhook,
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
  webhooks: Webhook,
  system: Monitor,
}

// --------------------------------------------------------------------------
// SettingField component
// --------------------------------------------------------------------------

function SettingField({
  setting,
  value,
  onChange,
}: {
  setting: SettingValue
  value: unknown
  onChange: (key: string, value: unknown) => void
}) {
  const isOverridden = value !== setting.default_value

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <label className="text-sm font-medium text-foreground">
          {setting.label}
        </label>
        {isOverridden && (
          <span className="inline-block h-2 w-2 rounded-full bg-primary" title="Overridden" />
        )}
      </div>
      <p className="text-sm text-muted-foreground">{setting.description}</p>

      {setting.value_type === 'bool' ? (
        <div className="flex items-center gap-2 pt-1">
          <button
            type="button"
            role="switch"
            aria-checked={value === true}
            onClick={() => onChange(setting.key, !value)}
            className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
              value ? 'bg-primary' : 'bg-muted'
            }`}
          >
            <span
              className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
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
          value={String(value)}
          onChange={(e) => onChange(setting.key, e.target.value)}
          className="flex h-10 w-full max-w-xs rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
        >
          {setting.options?.map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      ) : (
        <input
          type="number"
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
          className="flex h-10 w-full max-w-xs rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
        />
      )}

      <p className="text-xs text-muted-foreground">
        Default: {String(setting.default_value)}
        {setting.env_var && <> &middot; Env: {setting.env_var}</>}
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
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Monitor className="h-4 w-4" />
          System Information
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="rounded-md border border-blue-500/20 bg-blue-500/10 p-3 mb-4">
          <p className="text-sm text-blue-400">
            System settings are read-only and controlled by environment variables.
          </p>
        </div>
        <div className="divide-y divide-border">
          {settings.map((setting) => (
            <div
              key={setting.key}
              className="flex items-center justify-between py-3"
            >
              <span className="text-sm text-muted-foreground w-32 shrink-0">
                {setting.label}
              </span>
              <div className="flex items-center gap-2 min-w-0 flex-1 justify-end">
                <span className="text-sm font-mono truncate">
                  {String(setting.value)}
                </span>
                <button
                  type="button"
                  onClick={() => copyToClipboard(setting.key, String(setting.value))}
                  className="text-muted-foreground hover:text-foreground shrink-0 p-1"
                  title="Copy to clipboard"
                >
                  {copiedKey === setting.key ? (
                    <Check className="h-3.5 w-3.5 text-green-500" />
                  ) : (
                    <Copy className="h-3.5 w-3.5" />
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
  const [isResetDialogOpen, setIsResetDialogOpen] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  // Sync form state when data loads
  useEffect(() => {
    if (data?.settings) {
      const initial: Record<string, unknown> = {}
      for (const s of data.settings) {
        initial[s.key] = s.value
      }
      setFormValues(initial)
      setErrorMessage(null)
    }
  }, [data])

  const handleChange = useCallback((key: string, value: unknown) => {
    setFormValues((prev) => ({ ...prev, [key]: value }))
    setErrorMessage(null)
  }, [])

  // Compute dirty keys
  const dirtyKeys =
    data?.settings.filter((s) => formValues[s.key] !== s.value).map((s) => s.key) ?? []
  const isDirty = dirtyKeys.length > 0

  const handleCancel = useCallback(() => {
    if (data?.settings) {
      const initial: Record<string, unknown> = {}
      for (const s of data.settings) {
        initial[s.key] = s.value
      }
      setFormValues(initial)
      setErrorMessage(null)
    }
  }, [data])

  const handleSave = useCallback(async () => {
    if (!data) return
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
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2 text-base">
              <Icon className="h-4 w-4" />
              {data.label}
            </CardTitle>
            {hasOverrides && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setIsResetDialogOpen(true)}
                className="text-muted-foreground"
              >
                <RotateCcw className="h-3.5 w-3.5 mr-1.5" />
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
                  onChange={handleChange}
                />
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Sticky save bar */}
      {isDirty && (
        <div className="sticky bottom-0 mt-4 flex items-center justify-between rounded-lg border border-border bg-card p-4">
          <span className="flex items-center gap-2 text-sm text-muted-foreground">
            <span className="inline-block h-2 w-2 rounded-full bg-amber-500" />
            {dirtyKeys.length} unsaved {dirtyKeys.length === 1 ? 'change' : 'changes'}
          </span>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={handleCancel}>
              Cancel
            </Button>
            <Button
              size="sm"
              onClick={handleSave}
              disabled={updateMutation.isPending}
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
            <CardTitle>Reset {data.label}</CardTitle>
            <p className="text-sm text-muted-foreground">
              This will revert all settings in this section to their default values.
            </p>
          </CardHeader>
          <CardContent className="space-y-4">
            {data.settings.filter((s) => s.is_overridden).length > 0 && (
              <div className="rounded-md bg-muted p-3 text-sm font-mono space-y-1">
                {data.settings
                  .filter((s) => s.is_overridden)
                  .map((s) => (
                    <div key={s.key}>
                      {s.key}: {String(s.value)} &rarr; {String(s.default_value)}
                    </div>
                  ))}
              </div>
            )}
            <div className="flex justify-end gap-2">
              <Button
                variant="outline"
                onClick={() => setIsResetDialogOpen(false)}
              >
                Cancel
              </Button>
              <Button
                variant="destructive"
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
    <div className="space-y-6">
      {/* Page header */}
      <div>
        <h1 className="text-2xl font-bold">Settings</h1>
        <p className="text-muted-foreground">
          Manage system configuration and operational parameters
        </p>
      </div>

      {/* Tab bar */}
      <div className="overflow-x-auto">
        <div className="flex gap-1 border-b border-border">
          {namespacesLoading
            ? [1, 2, 3, 4, 5, 6].map((i) => (
                <Skeleton key={i} className="h-9 w-24" />
              ))
            : namespaces.map((ns) => {
                const Icon = NAMESPACE_ICONS[ns.namespace] ?? Gauge
                return (
                  <button
                    key={ns.namespace}
                    type="button"
                    onClick={() => handleTabChange(ns.namespace)}
                    className={`flex items-center gap-1.5 px-4 py-2 text-sm whitespace-nowrap border-b-2 transition-colors ${
                      activeTab === ns.namespace
                        ? 'border-primary text-foreground'
                        : 'border-transparent text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    <Icon className="h-3.5 w-3.5" />
                    {ns.label}
                    {ns.has_overrides && (
                      <span className="inline-block h-1.5 w-1.5 rounded-full bg-primary" />
                    )}
                  </button>
                )
              })}
        </div>
      </div>

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
