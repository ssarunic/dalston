import { useState } from 'react'
import { AlertCircle, AlertTriangle } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { useCreateApiKey } from '@/hooks/useApiKeys'
import type { APIKeyCreatedResponse } from '@/api/types'

interface CreateKeyDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated: (key: APIKeyCreatedResponse) => void
}

const AVAILABLE_SCOPES = [
  { value: 'jobs:read', label: 'Read Jobs', description: 'View job status and results' },
  { value: 'jobs:write', label: 'Create Jobs', description: 'Submit transcription jobs' },
  { value: 'realtime', label: 'Real-time', description: 'Connect to WebSocket streams' },
  { value: 'webhooks', label: 'Webhooks', description: 'Manage webhook configurations' },
  { value: 'admin', label: 'Admin Access', description: 'Full console access (grants all permissions)' },
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
      setError('Name is required')
      return
    }

    if (selectedScopes.length === 0) {
      setError('At least one scope is required')
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
        setError('Failed to create key')
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
          <CardTitle>Create API Key</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Name */}
            <div className="space-y-2">
              <label htmlFor="keyName" className="text-sm font-medium">
                Name
              </label>
              <input
                id="keyName"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g., Production API, CI Pipeline"
                className="w-full px-3 py-2 rounded-md border border-input bg-background text-sm"
                autoFocus
              />
            </div>

            {/* Scopes */}
            <div className="space-y-2">
              <label className="text-sm font-medium">Scopes</label>
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
                  <p className="font-medium text-orange-500">Admin scope selected</p>
                  <p className="text-muted-foreground">
                    This key will have full access to all API operations including key management.
                  </p>
                </div>
              </div>
            )}

            {/* Rate Limit (optional) */}
            <div className="space-y-2">
              <label htmlFor="rateLimit" className="text-sm font-medium">
                Rate Limit (optional)
              </label>
              <div className="flex items-center gap-2">
                <input
                  id="rateLimit"
                  type="number"
                  value={rateLimit}
                  onChange={(e) => setRateLimit(e.target.value)}
                  placeholder="Unlimited"
                  min="1"
                  max="10000"
                  className="w-32 px-3 py-2 rounded-md border border-input bg-background text-sm"
                />
                <span className="text-sm text-muted-foreground">requests/minute</span>
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
                Cancel
              </Button>
              <Button type="submit" disabled={createApiKey.isPending}>
                {createApiKey.isPending ? 'Creating...' : 'Create Key'}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
