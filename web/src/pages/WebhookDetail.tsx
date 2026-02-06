import { useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import {
  ArrowLeft,
  Webhook,
  AlertCircle,
  RefreshCw,
  Clock,
  CheckCircle,
  XCircle,
  ExternalLink,
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
import { useWebhooks, useWebhookDeliveries, useRetryDelivery } from '@/hooks/useWebhooks'
import type { WebhookDelivery } from '@/api/types'

const STATUS_CONFIG: Record<
  string,
  { icon: React.ElementType; color: string; bgColor: string }
> = {
  pending: { icon: Clock, color: 'text-yellow-500', bgColor: 'bg-yellow-500/10' },
  success: { icon: CheckCircle, color: 'text-green-500', bgColor: 'bg-green-500/10' },
  failed: { icon: XCircle, color: 'text-red-500', bgColor: 'bg-red-500/10' },
}

function DeliveryStatusBadge({ status }: { status: string }) {
  const config = STATUS_CONFIG[status] || STATUS_CONFIG.pending
  const Icon = config.icon

  return (
    <Badge variant="outline" className={`${config.bgColor} ${config.color} text-xs`}>
      <Icon className="h-3 w-3 mr-1" />
      {status}
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

function formatDateTime(dateStr: string): string {
  return new Date(dateStr).toLocaleString()
}

export function WebhookDetail() {
  const { endpointId } = useParams<{ endpointId: string }>()
  const [statusFilter, setStatusFilter] = useState<string | undefined>(undefined)
  const [page, setPage] = useState(0)
  const limit = 20

  const { data: webhooksData, isLoading: webhooksLoading } = useWebhooks()
  const {
    data: deliveriesData,
    isLoading: deliveriesLoading,
    error: deliveriesError,
  } = useWebhookDeliveries(endpointId!, {
    status: statusFilter,
    limit,
    offset: page * limit,
  })
  const retryDelivery = useRetryDelivery()

  // Find the webhook endpoint from the list
  const webhook = webhooksData?.endpoints.find((e) => e.id === endpointId)

  const handleRetry = async (delivery: WebhookDelivery) => {
    if (!endpointId) return
    try {
      await retryDelivery.mutateAsync({
        endpointId,
        deliveryId: delivery.id,
      })
    } catch (err) {
      console.error('Failed to retry delivery:', err)
    }
  }

  const deliveries = deliveriesData?.deliveries ?? []
  const total = deliveriesData?.total ?? 0
  const totalPages = Math.ceil(total / limit)

  if (webhooksLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    )
  }

  if (!webhook) {
    return (
      <div className="space-y-6">
        <Link
          to="/webhooks"
          className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="h-4 w-4" />
          Back to Webhooks
        </Link>
        <Card>
          <CardContent className="py-8">
            <div className="flex items-center justify-center gap-2 text-muted-foreground">
              <AlertCircle className="h-5 w-5" />
              <span>Webhook endpoint not found</span>
            </div>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Back Link */}
      <Link
        to="/webhooks"
        className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-4 w-4" />
        Back to Webhooks
      </Link>

      {/* Endpoint Info Card */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Webhook className="h-5 w-5" />
            Webhook Endpoint
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <p className="text-sm font-medium text-muted-foreground">URL</p>
              <p className="font-mono text-sm break-all">{webhook.url}</p>
            </div>
            <div>
              <p className="text-sm font-medium text-muted-foreground">Status</p>
              <div className="mt-1">
                {webhook.is_active ? (
                  <Badge variant="outline" className="bg-green-500/10 text-green-500">
                    Active
                  </Badge>
                ) : (
                  <Badge variant="outline" className="bg-gray-500/10 text-gray-500">
                    Inactive
                  </Badge>
                )}
              </div>
            </div>
            <div>
              <p className="text-sm font-medium text-muted-foreground">Events</p>
              <div className="flex flex-wrap gap-1 mt-1">
                {webhook.events.map((event) => (
                  <Badge key={event} variant="secondary" className="text-xs">
                    {event}
                  </Badge>
                ))}
              </div>
            </div>
            <div>
              <p className="text-sm font-medium text-muted-foreground">Created</p>
              <p className="text-sm">{formatDateTime(webhook.created_at)}</p>
            </div>
            {webhook.description && (
              <div className="md:col-span-2">
                <p className="text-sm font-medium text-muted-foreground">Description</p>
                <p className="text-sm">{webhook.description}</p>
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Deliveries Table */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>Delivery History</CardTitle>
          <div className="flex items-center gap-2">
            <select
              value={statusFilter || ''}
              onChange={(e) => {
                setStatusFilter(e.target.value || undefined)
                setPage(0)
              }}
              className="px-3 py-1.5 text-sm rounded-md border border-input bg-background"
            >
              <option value="">All statuses</option>
              <option value="pending">Pending</option>
              <option value="success">Success</option>
              <option value="failed">Failed</option>
            </select>
          </div>
        </CardHeader>
        <CardContent>
          {deliveriesLoading ? (
            <div className="space-y-3">
              {[...Array(5)].map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : deliveriesError ? (
            <div className="flex items-center gap-2 text-destructive py-4">
              <AlertCircle className="h-4 w-4" />
              <span>Failed to load deliveries</span>
            </div>
          ) : deliveries.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              <Clock className="h-12 w-12 mx-auto mb-4 opacity-50" />
              <p>No deliveries yet</p>
              <p className="text-sm mt-1">
                Deliveries will appear here when events are triggered
              </p>
            </div>
          ) : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Event</TableHead>
                    <TableHead>Job</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Attempts</TableHead>
                    <TableHead>Last Error</TableHead>
                    <TableHead>Created</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {deliveries.map((delivery) => (
                    <TableRow key={delivery.id}>
                      <TableCell>
                        <Badge variant="outline" className="text-xs">
                          {delivery.event_type}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        {delivery.job_id ? (
                          <Link
                            to={`/jobs/${delivery.job_id}`}
                            className="flex items-center gap-1 text-sm text-primary hover:underline"
                          >
                            {delivery.job_id.slice(0, 8)}...
                            <ExternalLink className="h-3 w-3" />
                          </Link>
                        ) : (
                          <span className="text-muted-foreground">-</span>
                        )}
                      </TableCell>
                      <TableCell>
                        <DeliveryStatusBadge status={delivery.status} />
                      </TableCell>
                      <TableCell>
                        <span className="text-sm">
                          {delivery.attempts}
                          {delivery.last_status_code && (
                            <span className="text-muted-foreground ml-1">
                              (HTTP {delivery.last_status_code})
                            </span>
                          )}
                        </span>
                      </TableCell>
                      <TableCell>
                        {delivery.last_error ? (
                          <span
                            className="text-sm text-destructive max-w-[200px] truncate block"
                            title={delivery.last_error}
                          >
                            {delivery.last_error}
                          </span>
                        ) : (
                          <span className="text-muted-foreground">-</span>
                        )}
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        {formatTimeAgo(delivery.created_at)}
                      </TableCell>
                      <TableCell className="text-right">
                        {delivery.status === 'failed' && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => handleRetry(delivery)}
                            disabled={retryDelivery.isPending}
                            title="Retry delivery"
                          >
                            <RefreshCw className="h-4 w-4" />
                          </Button>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>

              {/* Pagination */}
              {totalPages > 1 && (
                <div className="flex items-center justify-between mt-4 pt-4 border-t">
                  <p className="text-sm text-muted-foreground">
                    Showing {page * limit + 1}-{Math.min((page + 1) * limit, total)} of {total}
                  </p>
                  <div className="flex items-center gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setPage(page - 1)}
                      disabled={page === 0}
                    >
                      Previous
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setPage(page + 1)}
                      disabled={page >= totalPages - 1}
                    >
                      Next
                    </Button>
                  </div>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
