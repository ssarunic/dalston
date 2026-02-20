import { useEffect } from 'react'
import { useParams, Link } from 'react-router-dom'
import {
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
import { useTableState } from '@/hooks/useTableState'
import { useMediaQuery } from '@/hooks/useMediaQuery'
import { BackButton } from '@/components/BackButton'
import type { WebhookDelivery, DeliveryListResponse } from '@/api/types'

const PAGE_SIZE = 20

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
  const isMobile = useMediaQuery('(max-width: 767px)')
  const { endpointId } = useParams<{ endpointId: string }>()
  const {
    cursor,
    items: allDeliveries,
    filters,
    hasMore,
    setFilter,
    loadMore,
    processData,
    clearItems,
  } = useTableState<WebhookDelivery, DeliveryListResponse>({
    defaultFilters: { status: '' },
    dataKey: 'deliveries',
    getItems: (data) => data.deliveries,
    getCursor: (data) => data.cursor,
    getHasMore: (data) => data.has_more,
  })

  const statusFilter = filters.status || undefined

  const { data: webhooksData, isLoading: webhooksLoading } = useWebhooks()
  const {
    data: deliveriesData,
    isLoading: deliveriesLoading,
    isFetching,
    error: deliveriesError,
    refetch,
  } = useWebhookDeliveries(endpointId!, {
    status: statusFilter,
    limit: PAGE_SIZE,
    cursor,
  })
  const retryDelivery = useRetryDelivery()

  // Process data when it changes
  useEffect(() => {
    if (deliveriesData) {
      processData(deliveriesData)
    }
  }, [deliveriesData, processData])

  const handleFilterChange = (value: string | undefined) => {
    setFilter('status', value || '')
  }

  const handleRefresh = () => {
    clearItems()
    refetch()
  }

  // Find the webhook endpoint from the list
  const webhook = webhooksData?.endpoints.find((e) => e.id === endpointId)

  const handleRetry = async (delivery: WebhookDelivery) => {
    if (!endpointId) return
    try {
      await retryDelivery.mutateAsync({
        endpointId,
        deliveryId: delivery.id,
      })
      // Reset and refetch after retry
      clearItems()
      refetch()
    } catch (err) {
      console.error('Failed to retry delivery:', err)
    }
  }

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
        <BackButton fallbackPath="/webhooks" variant="link" label="Back to Webhooks" />
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
      <BackButton fallbackPath="/webhooks" variant="link" label="Back to Webhooks" />

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
              <div className="mt-1 flex flex-col gap-1">
                {webhook.is_active ? (
                  <Badge variant="outline" className="bg-green-500/10 text-green-500 w-fit">
                    Active
                  </Badge>
                ) : webhook.disabled_reason === 'auto_disabled' ? (
                  <Badge variant="outline" className="bg-orange-500/10 text-orange-500 border-orange-500/20 w-fit">
                    Auto-disabled
                  </Badge>
                ) : (
                  <Badge variant="outline" className="bg-gray-500/10 text-gray-500 w-fit">
                    Inactive
                  </Badge>
                )}
                {webhook.consecutive_failures > 0 && (
                  <span className="text-xs text-muted-foreground">
                    {webhook.consecutive_failures} consecutive failure{webhook.consecutive_failures !== 1 ? 's' : ''}
                  </span>
                )}
                {webhook.last_success_at && (
                  <span className="text-xs text-muted-foreground">
                    Last success: {formatTimeAgo(webhook.last_success_at)}
                  </span>
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
              onChange={(e) => handleFilterChange(e.target.value || undefined)}
              className="px-3 py-1.5 text-sm rounded-md border border-input bg-background"
            >
              <option value="">All statuses</option>
              <option value="pending">Pending</option>
              <option value="success">Success</option>
              <option value="failed">Failed</option>
            </select>
            <Button
              variant="outline"
              size="sm"
              onClick={handleRefresh}
              disabled={isFetching}
            >
              <RefreshCw className={`h-4 w-4 ${isFetching ? 'animate-spin' : ''}`} />
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {deliveriesLoading && cursor === undefined ? (
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
          ) : allDeliveries.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              <Clock className="h-12 w-12 mx-auto mb-4 opacity-50" />
              <p>No deliveries yet</p>
              <p className="text-sm mt-1">
                Deliveries will appear here when events are triggered
              </p>
            </div>
          ) : (
            <>
              {isMobile ? (
                <div className="space-y-3">
                  {allDeliveries.map((delivery) => (
                    <div key={delivery.id} className="rounded-lg border border-border p-3">
                      <div className="flex items-start justify-between gap-2">
                        <Badge variant="outline" className="text-xs">
                          {delivery.event_type}
                        </Badge>
                        <DeliveryStatusBadge status={delivery.status} />
                      </div>
                      <div className="mt-3 grid grid-cols-2 gap-3 text-sm">
                        <div>
                          <p className="text-xs text-muted-foreground">Job</p>
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
                        </div>
                        <div>
                          <p className="text-xs text-muted-foreground">Created</p>
                          <p>{formatTimeAgo(delivery.created_at)}</p>
                        </div>
                        <div>
                          <p className="text-xs text-muted-foreground">Attempts</p>
                          <p>
                            {delivery.attempts}
                            {delivery.last_status_code ? ` (HTTP ${delivery.last_status_code})` : ''}
                          </p>
                        </div>
                        <div>
                          <p className="text-xs text-muted-foreground">Last Error</p>
                          <p className="text-destructive break-words">{delivery.last_error || '-'}</p>
                        </div>
                      </div>
                      {delivery.status === 'failed' && (
                        <div className="mt-3 flex justify-end">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => handleRetry(delivery)}
                            disabled={retryDelivery.isPending}
                          >
                            <RefreshCw className="h-4 w-4 mr-1" />
                            Retry
                          </Button>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <Table className="min-w-[980px]">
                  <TableHeader>
                    <TableRow>
                      <TableHead className="sticky left-0 z-10 bg-card">Event</TableHead>
                      <TableHead>Job</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead>Attempts</TableHead>
                      <TableHead>Last Error</TableHead>
                      <TableHead>Created</TableHead>
                      <TableHead className="sticky right-0 z-10 bg-card text-right">Actions</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {allDeliveries.map((delivery) => (
                      <TableRow key={delivery.id}>
                        <TableCell className="sticky left-0 z-10 bg-card">
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
                        <TableCell className="text-right sticky right-0 z-10 bg-card">
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
              )}

              {/* Pagination */}
              {allDeliveries.length > 0 && (
                <div className="flex flex-col items-center gap-3 pt-4 mt-4 border-t">
                  <p className="text-sm text-muted-foreground">
                    Showing {allDeliveries.length} deliveries
                  </p>
                  {hasMore && (
                    <Button
                      variant="outline"
                      onClick={loadMore}
                      disabled={isFetching}
                    >
                      {isFetching ? 'Loading...' : 'Load More'}
                    </Button>
                  )}
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
