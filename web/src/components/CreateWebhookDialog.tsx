import { useState } from 'react'
import { AlertCircle } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { useCreateWebhook } from '@/hooks/useWebhooks'
import { S } from '@/lib/strings'
import type { WebhookEndpointCreated } from '@/api/types'

interface CreateWebhookDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onCreated: (webhook: WebhookEndpointCreated) => void
}

const AVAILABLE_EVENTS = [
  {
    value: 'transcription.completed',
    label: S.createWebhookDialog.events.completed.label,
    description: S.createWebhookDialog.events.completed.description,
  },
  {
    value: 'transcription.failed',
    label: S.createWebhookDialog.events.failed.label,
    description: S.createWebhookDialog.events.failed.description,
  },
  {
    value: 'transcription.cancelled',
    label: S.createWebhookDialog.events.cancelled.label,
    description: S.createWebhookDialog.events.cancelled.description,
  },
  {
    value: '*',
    label: S.createWebhookDialog.events.all.label,
    description: S.createWebhookDialog.events.all.description,
  },
]

export function CreateWebhookDialog({ open, onOpenChange, onCreated }: CreateWebhookDialogProps) {
  const [url, setUrl] = useState('')
  const [description, setDescription] = useState('')
  const [selectedEvents, setSelectedEvents] = useState<string[]>([
    'transcription.completed',
    'transcription.failed',
    'transcription.cancelled',
  ])
  const [error, setError] = useState<string | null>(null)

  const createWebhook = useCreateWebhook()

  const handleEventToggle = (event: string) => {
    if (event === '*') {
      // Wildcard is exclusive
      if (selectedEvents.includes('*')) {
        setSelectedEvents(['transcription.completed', 'transcription.failed', 'transcription.cancelled'])
      } else {
        setSelectedEvents(['*'])
      }
    } else {
      if (selectedEvents.includes('*')) {
        // If wildcard is selected, switch to this event
        setSelectedEvents([event])
      } else if (selectedEvents.includes(event)) {
        // Remove event (but keep at least one)
        const newEvents = selectedEvents.filter((e) => e !== event)
        if (newEvents.length > 0) {
          setSelectedEvents(newEvents)
        }
      } else {
        // Add event
        setSelectedEvents([...selectedEvents, event])
      }
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)

    if (!url.trim()) {
      setError(S.errors.urlRequired)
      return
    }

    // Basic URL validation
    try {
      new URL(url)
    } catch {
      setError(S.errors.invalidUrlFormat)
      return
    }

    if (selectedEvents.length === 0) {
      setError(S.errors.eventRequired)
      return
    }

    try {
      const result = await createWebhook.mutateAsync({
        url: url.trim(),
        events: selectedEvents,
        description: description.trim() || undefined,
      })
      // Reset form
      setUrl('')
      setDescription('')
      setSelectedEvents(['transcription.completed', 'transcription.failed', 'transcription.cancelled'])
      onCreated(result)
    } catch (err) {
      if (err instanceof Error) {
        setError(err.message)
      } else {
        setError(S.errors.failedToCreateWebhook)
      }
    }
  }

  const handleClose = () => {
    setUrl('')
    setDescription('')
    setSelectedEvents(['transcription.completed', 'transcription.failed', 'transcription.cancelled'])
    setError(null)
    onOpenChange(false)
  }

  if (!open) return null

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <Card className="w-full max-w-lg mx-4">
        <CardHeader>
          <CardTitle>{S.createWebhookDialog.title}</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            {/* URL */}
            <div className="space-y-2">
              <label htmlFor="webhookUrl" className="text-sm font-medium">
                {S.createWebhookDialog.urlLabel}
              </label>
              <input
                id="webhookUrl"
                type="url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder={S.createWebhookDialog.urlPlaceholder}
                className="w-full px-3 py-2 rounded-md border border-input bg-background text-sm"
                autoFocus
              />
              <p className="text-xs text-muted-foreground">
                {S.createWebhookDialog.urlHint}
              </p>
            </div>

            {/* Description (optional) */}
            <div className="space-y-2">
              <label htmlFor="webhookDescription" className="text-sm font-medium">
                {S.createWebhookDialog.descriptionLabel}
              </label>
              <input
                id="webhookDescription"
                type="text"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder={S.createWebhookDialog.descriptionPlaceholder}
                className="w-full px-3 py-2 rounded-md border border-input bg-background text-sm"
              />
            </div>

            {/* Events */}
            <div className="space-y-2">
              <label className="text-sm font-medium">{S.createWebhookDialog.eventsLabel}</label>
              <div className="space-y-2">
                {AVAILABLE_EVENTS.map((event) => (
                  <label
                    key={event.value}
                    className={`flex items-start gap-3 p-3 rounded-md border cursor-pointer transition-colors ${
                      selectedEvents.includes(event.value)
                        ? 'border-primary bg-primary/5'
                        : 'border-input hover:bg-accent'
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={selectedEvents.includes(event.value)}
                      onChange={() => handleEventToggle(event.value)}
                      className="mt-0.5 rounded"
                    />
                    <div className="flex-1">
                      <span className="font-medium text-sm">{event.label}</span>
                      <p className="text-xs text-muted-foreground">{event.description}</p>
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
                {S.common.cancel}
              </Button>
              <Button type="submit" disabled={createWebhook.isPending}>
                {createWebhook.isPending ? S.createWebhookDialog.creating : S.webhooks.createWebhook}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
