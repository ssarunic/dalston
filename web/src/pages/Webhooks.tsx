import { useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Webhook,
  Plus,
  Trash2,
  AlertCircle,
  RefreshCw,
  ExternalLink,
  ToggleLeft,
  ToggleRight,
} from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  useWebhooks,
  useDeleteWebhook,
  useUpdateWebhook,
  useRotateWebhookSecret,
} from '@/hooks/useWebhooks'
import { CreateWebhookDialog } from '@/components/CreateWebhookDialog'
import { WebhookSecretModal } from '@/components/WebhookSecretModal'
import type { WebhookEndpoint, WebhookEndpointCreated } from '@/api/types'

const EVENT_COLORS: Record<string, string> = {
  'transcription.completed': 'bg-green-500/10 text-green-500 border-green-500/20',
  'transcription.failed': 'bg-red-500/10 text-red-500 border-red-500/20',
  '*': 'bg-purple-500/10 text-purple-500 border-purple-500/20',
}

function EventBadge({ event }: { event: string }) {
  const colorClass = EVENT_COLORS[event] || 'bg-gray-500/10 text-gray-500 border-gray-500/20'
  return (
    <Badge variant="outline" className={`text-xs ${colorClass}`}>
      {event}
    </Badge>
  )
}

function formatTimeAgo(dateStr: string): string {
  const date = new Date(dateStr)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)

  if (diffMins < 1) return 'just now'
  if (diffMins < 60) return `${diffMins}m ago`
  const diffHours = Math.floor(diffMins / 60)
  if (diffHours < 24) return `${diffHours}h ago`
  const diffDays = Math.floor(diffHours / 24)
  if (diffDays < 30) return `${diffDays}d ago`
  const diffMonths = Math.floor(diffDays / 30)
  return `${diffMonths}mo ago`
}

function truncateUrl(url: string, maxLength = 50): string {
  if (url.length <= maxLength) return url
  return url.slice(0, maxLength - 3) + '...'
}

export function Webhooks() {
  const [showInactive, setShowInactive] = useState(true)
  const [createDialogOpen, setCreateDialogOpen] = useState(false)
  const [createdWebhook, setCreatedWebhook] = useState<WebhookEndpointCreated | null>(null)
  const [rotatedWebhook, setRotatedWebhook] = useState<WebhookEndpointCreated | null>(null)
  const [deleteConfirm, setDeleteConfirm] = useState<WebhookEndpoint | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  const { data, isLoading, error } = useWebhooks(showInactive ? undefined : true)
  const deleteWebhook = useDeleteWebhook()
  const updateWebhook = useUpdateWebhook()
  const rotateSecret = useRotateWebhookSecret()

  const handleWebhookCreated = (webhook: WebhookEndpointCreated) => {
    setCreateDialogOpen(false)
    setCreatedWebhook(webhook)
  }

  const handleDelete = async (webhook: WebhookEndpoint) => {
    setDeleteError(null)
    try {
      await deleteWebhook.mutateAsync(webhook.id)
      setDeleteConfirm(null)
    } catch (err) {
      if (err instanceof Error) {
        setDeleteError(err.message)
      } else {
        setDeleteError('Failed to delete webhook')
      }
    }
  }

  const handleToggleActive = async (webhook: WebhookEndpoint) => {
    try {
      await updateWebhook.mutateAsync({
        id: webhook.id,
        request: { is_active: !webhook.is_active },
      })
    } catch (err) {
      console.error('Failed to toggle webhook status:', err)
    }
  }

  const handleRotateSecret = async (webhook: WebhookEndpoint) => {
    try {
      const result = await rotateSecret.mutateAsync(webhook.id)
      setRotatedWebhook(result)
    } catch (err) {
      console.error('Failed to rotate secret:', err)
    }
  }

  const webhooks = data?.endpoints ?? []

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Webhooks</h1>
          <p className="text-muted-foreground">Manage webhook endpoints for event notifications</p>
        </div>
        <Button onClick={() => setCreateDialogOpen(true)}>
          <Plus className="h-4 w-4 mr-2" />
          Create Webhook
        </Button>
      </div>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="flex items-center gap-2">
            <Webhook className="h-5 w-5" />
            Webhook Endpoints
          </CardTitle>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={showInactive}
              onChange={(e) => setShowInactive(e.target.checked)}
              className="rounded border-input"
            />
            Show inactive
          </label>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-3">
              {[...Array(3)].map((_, i) => (
                <Skeleton key={i} className="h-16 w-full" />
              ))}
            </div>
          ) : error ? (
            <div className="flex items-center gap-2 text-destructive py-4">
              <AlertCircle className="h-4 w-4" />
              <span>Failed to load webhooks</span>
            </div>
          ) : webhooks.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              <Webhook className="h-12 w-12 mx-auto mb-4 opacity-50" />
              <p>No webhook endpoints found</p>
              <p className="text-sm mt-1">Create an endpoint to receive event notifications</p>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>URL</TableHead>
                  <TableHead>Events</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {webhooks.map((webhook) => (
                  <TableRow
                    key={webhook.id}
                    className={!webhook.is_active ? 'opacity-50' : undefined}
                  >
                    <TableCell>
                      <div className="flex flex-col">
                        <span className="font-mono text-sm" title={webhook.url}>
                          {truncateUrl(webhook.url)}
                        </span>
                        {webhook.description && (
                          <span className="text-xs text-muted-foreground">
                            {webhook.description}
                          </span>
                        )}
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {webhook.events.map((event) => (
                          <EventBadge key={event} event={event} />
                        ))}
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-col gap-1">
                        {webhook.is_active ? (
                          <Badge variant="outline" className="text-xs bg-green-500/10 text-green-500">
                            Active
                          </Badge>
                        ) : webhook.disabled_reason === 'auto_disabled' ? (
                          <Badge variant="outline" className="text-xs bg-orange-500/10 text-orange-500 border-orange-500/20">
                            Auto-disabled
                          </Badge>
                        ) : (
                          <Badge variant="outline" className="text-xs bg-gray-500/10 text-gray-500">
                            Inactive
                          </Badge>
                        )}
                        {webhook.consecutive_failures > 0 && (
                          <span className="text-xs text-muted-foreground">
                            {webhook.consecutive_failures} consecutive failure{webhook.consecutive_failures !== 1 ? 's' : ''}
                          </span>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {formatTimeAgo(webhook.created_at)}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex items-center justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleToggleActive(webhook)}
                          title={webhook.is_active ? 'Deactivate' : 'Activate'}
                          disabled={updateWebhook.isPending}
                        >
                          {webhook.is_active ? (
                            <ToggleRight className="h-4 w-4 text-green-500" />
                          ) : (
                            <ToggleLeft className="h-4 w-4" />
                          )}
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleRotateSecret(webhook)}
                          title="Rotate secret"
                          disabled={rotateSecret.isPending}
                        >
                          <RefreshCw className="h-4 w-4" />
                        </Button>
                        <Link to={`/webhooks/${webhook.id}`}>
                          <Button variant="ghost" size="sm" title="View deliveries">
                            <ExternalLink className="h-4 w-4" />
                          </Button>
                        </Link>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setDeleteConfirm(webhook)}
                          title="Delete"
                          className="text-destructive hover:text-destructive"
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Create Webhook Dialog */}
      <CreateWebhookDialog
        open={createDialogOpen}
        onOpenChange={setCreateDialogOpen}
        onCreated={handleWebhookCreated}
      />

      {/* Webhook Created Modal */}
      <WebhookSecretModal webhook={createdWebhook} onClose={() => setCreatedWebhook(null)} />

      {/* Secret Rotated Modal */}
      <WebhookSecretModal
        webhook={rotatedWebhook}
        onClose={() => setRotatedWebhook(null)}
        isRotation
      />

      {/* Delete Confirmation Dialog */}
      {deleteConfirm && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <Card className="w-full max-w-md mx-4">
            <CardHeader>
              <CardTitle className="text-destructive">Delete Webhook</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-muted-foreground">
                Are you sure you want to delete this webhook? This will also delete all delivery
                history. This action cannot be undone.
              </p>
              <div className="bg-muted p-3 rounded-md">
                <p className="font-mono text-sm break-all">{deleteConfirm.url}</p>
                {deleteConfirm.description && (
                  <p className="text-sm text-muted-foreground mt-1">{deleteConfirm.description}</p>
                )}
              </div>
              {deleteError && (
                <div className="flex items-center gap-2 text-sm text-destructive">
                  <AlertCircle className="h-4 w-4" />
                  <span>{deleteError}</span>
                </div>
              )}
              <div className="flex justify-end gap-2">
                <Button
                  variant="outline"
                  onClick={() => {
                    setDeleteConfirm(null)
                    setDeleteError(null)
                  }}
                >
                  Cancel
                </Button>
                <Button
                  variant="destructive"
                  onClick={() => handleDelete(deleteConfirm)}
                  disabled={deleteWebhook.isPending}
                >
                  {deleteWebhook.isPending ? 'Deleting...' : 'Delete Webhook'}
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  )
}
