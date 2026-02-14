import { useState } from 'react'
import { AlertCircle } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { useCreateRetentionPolicy } from '@/hooks/useRetentionPolicies'
import type { RetentionPolicy, RetentionMode, RetentionScope } from '@/api/types'

interface CreatePolicyDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated: (policy: RetentionPolicy) => void
}

const RETENTION_MODES: { value: RetentionMode; label: string; description: string }[] = [
  { value: 'auto_delete', label: 'Auto Delete', description: 'Automatically delete after specified hours' },
  { value: 'keep', label: 'Keep Forever', description: 'Never automatically delete' },
  { value: 'none', label: 'Zero Retention', description: 'Delete immediately after processing' },
]

const RETENTION_SCOPES: { value: RetentionScope; label: string; description: string }[] = [
  { value: 'all', label: 'All Artifacts', description: 'Delete audio and transcripts' },
  { value: 'audio_only', label: 'Audio Only', description: 'Delete audio, keep transcripts' },
]

export function CreatePolicyDialog({ open, onOpenChange, onCreated }: CreatePolicyDialogProps) {
  const [name, setName] = useState('')
  const [mode, setMode] = useState<RetentionMode>('auto_delete')
  const [hours, setHours] = useState<string>('24')
  const [scope, setScope] = useState<RetentionScope>('all')
  const [error, setError] = useState<string | null>(null)

  const createPolicy = useCreateRetentionPolicy()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)

    if (!name.trim()) {
      setError('Name is required')
      return
    }

    if (mode === 'auto_delete' && (!hours || parseInt(hours, 10) < 1)) {
      setError('Hours is required for auto_delete mode (minimum 1)')
      return
    }

    try {
      const result = await createPolicy.mutateAsync({
        name: name.trim(),
        mode,
        hours: mode === 'auto_delete' ? parseInt(hours, 10) : null,
        scope,
      })
      // Reset form
      setName('')
      setMode('auto_delete')
      setHours('24')
      setScope('all')
      onCreated(result)
    } catch (err) {
      if (err instanceof Error) {
        setError(err.message)
      } else {
        setError('Failed to create policy')
      }
    }
  }

  const handleClose = () => {
    setName('')
    setMode('auto_delete')
    setHours('24')
    setScope('all')
    setError(null)
    onOpenChange(false)
  }

  if (!open) return null

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <Card className="w-full max-w-lg mx-4">
        <CardHeader>
          <CardTitle>Create Retention Policy</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Name */}
            <div className="space-y-2">
              <label htmlFor="policyName" className="text-sm font-medium">
                Name
              </label>
              <input
                id="policyName"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g., hipaa-6yr, short-term"
                className="w-full px-3 py-2 rounded-md border border-input bg-background text-sm"
                autoFocus
              />
            </div>

            {/* Mode */}
            <div className="space-y-2">
              <label className="text-sm font-medium">Retention Mode</label>
              <div className="space-y-2">
                {RETENTION_MODES.map((option) => (
                  <label
                    key={option.value}
                    className={`flex items-start gap-3 p-3 rounded-md border cursor-pointer transition-colors ${
                      mode === option.value
                        ? 'border-primary bg-primary/5'
                        : 'border-input hover:bg-accent'
                    }`}
                  >
                    <input
                      type="radio"
                      name="mode"
                      checked={mode === option.value}
                      onChange={() => setMode(option.value)}
                      className="mt-0.5"
                    />
                    <div className="flex-1">
                      <span className="font-medium text-sm">{option.label}</span>
                      <p className="text-xs text-muted-foreground">{option.description}</p>
                    </div>
                  </label>
                ))}
              </div>
            </div>

            {/* Hours (only for auto_delete) */}
            {mode === 'auto_delete' && (
              <div className="space-y-2">
                <label htmlFor="hours" className="text-sm font-medium">
                  Retention Period
                </label>
                <div className="flex items-center gap-2">
                  <input
                    id="hours"
                    type="number"
                    value={hours}
                    onChange={(e) => setHours(e.target.value)}
                    min="1"
                    className="w-24 px-3 py-2 rounded-md border border-input bg-background text-sm"
                  />
                  <span className="text-sm text-muted-foreground">hours</span>
                  <span className="text-xs text-muted-foreground ml-2">
                    ({Math.floor(parseInt(hours || '0', 10) / 24)} days)
                  </span>
                </div>
              </div>
            )}

            {/* Scope */}
            <div className="space-y-2">
              <label className="text-sm font-medium">Deletion Scope</label>
              <div className="space-y-2">
                {RETENTION_SCOPES.map((option) => (
                  <label
                    key={option.value}
                    className={`flex items-start gap-3 p-3 rounded-md border cursor-pointer transition-colors ${
                      scope === option.value
                        ? 'border-primary bg-primary/5'
                        : 'border-input hover:bg-accent'
                    }`}
                  >
                    <input
                      type="radio"
                      name="scope"
                      checked={scope === option.value}
                      onChange={() => setScope(option.value)}
                      className="mt-0.5"
                    />
                    <div className="flex-1">
                      <span className="font-medium text-sm">{option.label}</span>
                      <p className="text-xs text-muted-foreground">{option.description}</p>
                    </div>
                  </label>
                ))}
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
              <Button type="submit" disabled={createPolicy.isPending}>
                {createPolicy.isPending ? 'Creating...' : 'Create Policy'}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
