import { useState } from 'react'
import { AlertCircle, AlertTriangle } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { useCreateApiKey } from '@/hooks/useApiKeys'
import type { APIKeyCreatedResponse } from '@/api/types'
import { S } from '@/lib/strings'

interface CreateKeyDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated: (key: APIKeyCreatedResponse) => void
}

const AVAILABLE_SCOPES = [
  { value: 'jobs:read', label: S.createKeyDialog.scopes.read.label, description: S.createKeyDialog.scopes.read.description },
  { value: 'jobs:write', label: S.createKeyDialog.scopes.create.label, description: S.createKeyDialog.scopes.create.description },
  { value: 'realtime', label: S.createKeyDialog.scopes.realtime.label, description: S.createKeyDialog.scopes.realtime.description },
  { value: 'webhooks', label: S.createKeyDialog.scopes.webhooks.label, description: S.createKeyDialog.scopes.webhooks.description },
  { value: 'admin', label: S.createKeyDialog.scopes.admin.label, description: S.createKeyDialog.scopes.admin.description },
]

const DEFAULT_SCOPES = ['jobs:read', 'jobs:write', 'realtime']

export function CreateKeyDialog({ open, onOpenChange, onCreated }: CreateKeyDialogProps) {
  const [name, setName] = useState('')
  const [selectedScopes, setSelectedScopes] = useState<string[]>(DEFAULT_SCOPES)
  const [rateLimit, setRateLimit] = useState<string>('')
  const [error, setError] = useState<string | null>(null)

  const createApiKey = useCreateApiKey()

  const handleScopeToggle = (scope: string) => {
    if (scope === 'admin') {
      // Admin scope is exclusive - select only admin
      if (selectedScopes.includes('admin')) {
        setSelectedScopes(DEFAULT_SCOPES)
      } else {
        setSelectedScopes(['admin'])
      }
    } else {
      // Regular scope toggle
      if (selectedScopes.includes('admin')) {
        // If admin is selected, switch to this scope
        setSelectedScopes([scope])
      } else if (selectedScopes.includes(scope)) {
        // Remove scope (but keep at least one)
        const newScopes = selectedScopes.filter((s) => s !== scope)
        if (newScopes.length > 0) {
          setSelectedScopes(newScopes)
        }
      } else {
        // Add scope
        setSelectedScopes([...selectedScopes, scope])
      }
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)

    if (!name.trim()) {
      setError(S.errors.nameRequired)
      return
    }

    if (selectedScopes.length === 0) {
      setError(S.errors.scopeRequired)
      return
    }

    try {
      const result = await createApiKey.mutateAsync({
        name: name.trim(),
        scopes: selectedScopes,
        rate_limit: rateLimit ? parseInt(rateLimit, 10) : null,
      })
      // Reset form
      setName('')
      setSelectedScopes(DEFAULT_SCOPES)
      setRateLimit('')
      onCreated(result)
    } catch (err) {
      if (err instanceof Error) {
        setError(err.message)
      } else {
        setError(S.errors.failedToCreateKey)
      }
    }
  }

  const handleClose = () => {
    setName('')
    setSelectedScopes(DEFAULT_SCOPES)
    setRateLimit('')
    setError(null)
    onOpenChange(false)
  }

  if (!open) return null

  const hasAdminScope = selectedScopes.includes('admin')

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <Card className="w-full max-w-lg mx-4">
        <CardHeader>
          <CardTitle>{S.createKeyDialog.title}</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Name */}
            <div className="space-y-2">
              <label htmlFor="keyName" className="text-sm font-medium">
                {S.createKeyDialog.nameLabel}
              </label>
              <input
                id="keyName"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={S.createKeyDialog.namePlaceholder}
                className="w-full px-3 py-2 rounded-md border border-input bg-background text-sm"
                autoFocus
              />
            </div>

            {/* Scopes */}
            <div className="space-y-2">
              <label className="text-sm font-medium">{S.createKeyDialog.scopesLabel}</label>
              <div className="space-y-2">
                {AVAILABLE_SCOPES.map((scope) => (
                  <label
                    key={scope.value}
                    className={`flex items-start gap-3 p-3 rounded-md border cursor-pointer transition-colors ${
                      selectedScopes.includes(scope.value)
                        ? 'border-primary bg-primary/5'
                        : 'border-input hover:bg-accent'
                    } ${scope.value === 'admin' ? 'border-orange-500/50' : ''}`}
                  >
                    <input
                      type="checkbox"
                      checked={selectedScopes.includes(scope.value)}
                      onChange={() => handleScopeToggle(scope.value)}
                      className="mt-0.5 rounded"
                    />
                    <div className="flex-1">
                      <div className="flex items-center gap-2">
                        <span className="font-medium text-sm">{scope.label}</span>
                        {scope.value === 'admin' && (
                          <AlertTriangle className="h-3.5 w-3.5 text-orange-500" />
                        )}
                      </div>
                      <p className="text-xs text-muted-foreground">{scope.description}</p>
                    </div>
                  </label>
                ))}
              </div>
            </div>

            {/* Admin warning */}
            {hasAdminScope && (
              <div className="flex items-start gap-2 p-3 rounded-md bg-orange-500/10 border border-orange-500/20 text-sm">
                <AlertTriangle className="h-4 w-4 text-orange-500 mt-0.5" />
                <div>
                  <p className="font-medium text-orange-500">{S.createKeyDialog.adminWarningTitle}</p>
                  <p className="text-muted-foreground">
                    {S.createKeyDialog.adminWarningText}
                  </p>
                </div>
              </div>
            )}

            {/* Rate Limit (optional) */}
            <div className="space-y-2">
              <label htmlFor="rateLimit" className="text-sm font-medium">
                {S.createKeyDialog.rateLimitLabel}
              </label>
              <div className="flex items-center gap-2">
                <input
                  id="rateLimit"
                  type="number"
                  value={rateLimit}
                  onChange={(e) => setRateLimit(e.target.value)}
                  placeholder={S.createKeyDialog.rateLimitPlaceholder}
                  min="1"
                  max="10000"
                  className="w-32 px-3 py-2 rounded-md border border-input bg-background text-sm"
                />
                <span className="text-sm text-muted-foreground">{S.createKeyDialog.rateLimitUnit}</span>
              </div>
            </div>

            {/* Error */}
            {error && (
              <div className="flex items-center gap-2 text-sm text-destructive">
                <AlertCircle className="h-4 w-4" />
                <span>{error}</span>
              </div>
            )}

            {/* Actions */}
            <div className="flex justify-end gap-2 pt-2">
              <Button type="button" variant="outline" onClick={handleClose}>
                {S.common.cancel}
              </Button>
              <Button type="submit" disabled={createApiKey.isPending}>
                {createApiKey.isPending ? S.createKeyDialog.creating : S.apiKeys.createKey}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
