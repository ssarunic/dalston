import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Radio, MessageSquare, Mic, Trash2, RefreshCw, Filter, X } from 'lucide-react'
import { apiClient } from '@/api/client'
import { useMediaQuery } from '@/hooks/useMediaQuery'
import { useSharedTableState } from '@/hooks/useSharedTableState'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent } from '@/components/ui/dialog'
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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Badge } from '@/components/ui/badge'
import { StatusBadge } from '@/components/StatusBadge'
import { ListLoadMoreFooter } from '@/components/ListLoadMoreFooter'
import { useRealtimeStatus } from '@/hooks/useRealtimeStatus'
import { useRealtimeSessions } from '@/hooks/useRealtimeSessions'
import type { RealtimeStatusResponse } from '@/api/types'

function StatusDot({ status }: { status: string }) {
  const color =
    status === 'ready'
      ? 'bg-green-500'
      : status === 'at_capacity'
        ? 'bg-yellow-500'
        : 'bg-red-500'
  return <span className={`inline-block w-3 h-3 rounded-full ${color}`} />
}

function formatDuration(seconds: number): string {
  if (seconds < 60) {
    return `${Math.round(seconds)}s`
  }
  const mins = Math.floor(seconds / 60)
  const secs = Math.round(seconds % 60)
  return `${mins}m ${secs}s`
}

function formatDate(dateStr: string): string {
  const date = new Date(dateStr)
  return date.toLocaleString()
}

const DEFAULT_PAGE_SIZE = 50
const PAGE_SIZE_OPTIONS = [20, 50, 100] as const
const STATUS_OPTIONS = ['all', 'active', 'completed', 'error', 'interrupted'] as const
const SORT_OPTION_VALUES = ['started_desc', 'started_asc'] as const
const SORT_OPTIONS = [
  { label: 'Newest first', value: 'started_desc' },
  { label: 'Oldest first', value: 'started_asc' },
] as const

interface StatusGuidance {
  title: string
  summary: string
  details: string
  level: 'normal' | 'warning' | 'error'
  showStartWorker: boolean
}

function buildStatusGuidance(statusData?: RealtimeStatusResponse): StatusGuidance {
  if (!statusData) {
    return {
      title: 'Status not available',
      summary: 'Realtime status data has not loaded yet.',
      details: 'Refresh status and check engine health if this persists.',
      level: 'warning',
      showStartWorker: false,
    }
  }

  if (statusData.status === 'unavailable') {
    if (statusData.worker_count === 0) {
      return {
        title: 'No realtime workers running',
        summary: 'Realtime is unavailable because no workers are registered.',
        details:
          'Start at least one realtime worker, then refresh this page. If you run on AWS/ECS/Kubernetes, scale the worker service/deployment instead of using local Docker commands.',
        level: 'error',
        showStartWorker: true,
      }
    }

    return {
      title: 'Workers unhealthy',
      summary: `Realtime is unavailable: 0/${statusData.worker_count} workers are ready.`,
      details:
        'Workers are registered but not healthy. Check worker logs, model loading, and health checks.',
      level: 'error',
      showStartWorker: false,
    }
  }

  if (statusData.status === 'at_capacity') {
    return {
      title: 'At capacity',
      summary:
        `All available capacity is currently in use (${statusData.active_sessions}/${statusData.total_capacity}).`,
      details: 'Wait for active sessions to finish or scale workers to increase available capacity.',
      level: 'warning',
      showStartWorker: false,
    }
  }

  return {
    title: 'Realtime healthy',
    summary: `Workers are ready and can accept new sessions (${statusData.ready_workers}/${statusData.worker_count} ready).`,
    details: 'No action needed unless you expect higher throughput.',
    level: 'normal',
    showStartWorker: false,
  }
}

export function RealtimeSessions() {
  const isMobile = useMediaQuery('(max-width: 767px)')
  const navigate = useNavigate()
  const {
    status: statusFilter,
    sort,
    limit,
    setStatus,
    setSort,
    setLimit,
    resetAll,
  } = useSharedTableState({
    defaultStatus: 'all',
    statusOptions: STATUS_OPTIONS,
    defaultSort: 'started_desc',
    sortOptions: SORT_OPTION_VALUES,
    defaultLimit: DEFAULT_PAGE_SIZE,
    limitOptions: PAGE_SIZE_OPTIONS,
  })
  const [deleteTarget, setDeleteTarget] = useState<{ id: string } | null>(null)
  const [isDeleting, setIsDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [showFilters, setShowFilters] = useState(false)
  const [showStatusWhy, setShowStatusWhy] = useState(false)

  const hasActiveFilters =
    statusFilter !== 'all' ||
    sort !== 'started_desc' ||
    limit !== DEFAULT_PAGE_SIZE
  const { data: statusData, isLoading: statusLoading, error: statusError } = useRealtimeStatus()
  const statusGuidance = useMemo(() => buildStatusGuidance(statusData), [statusData])
  const {
    data: sessionsData,
    isLoading: sessionsLoading,
    isFetching,
    isFetchingNextPage,
    hasNextPage,
    fetchNextPage,
    refetch,
  } = useRealtimeSessions({
    status: statusFilter === 'all' ? undefined : statusFilter,
    limit,
    sort,
  })
  const allSessions = useMemo(() => sessionsData?.pages.flatMap((page) => page.sessions) ?? [], [sessionsData])
  const visibleSessions = allSessions

  const handleFilterChange = (value: string) => {
    setStatus(value)
  }

  const handleSortChange = (value: string) => {
    setSort(value)
  }

  const handleLimitChange = (value: string) => {
    setLimit(Number(value))
  }

  const handleRefresh = async () => {
    await refetch()
  }

  const loadMore = () => {
    if (!hasNextPage || isFetchingNextPage) return
    void fetchNextPage()
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    setIsDeleting(true)
    setDeleteError(null)
    try {
      await apiClient.deleteRealtimeSession(deleteTarget.id)
      setDeleteTarget(null)
      await refetch()
    } catch (error) {
      const message =
        error instanceof Error ? error.message : 'Failed to delete session'
      setDeleteError(message)
    } finally {
      setIsDeleting(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Realtime</h1>
          <p className="text-muted-foreground">
            Real-time transcription workers, capacity, and session history
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setShowFilters(!showFilters)}
          >
            <Filter className="h-4 w-4 mr-2" />
            Filters
            {hasActiveFilters && (
              <Badge variant="secondary" className="ml-2">
                Active
              </Badge>
            )}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={handleRefresh}
            disabled={isFetching}
          >
            <RefreshCw className={`h-4 w-4 mr-2 ${isFetching ? 'animate-spin' : ''}`} />
            Refresh
          </Button>
        </div>
      </div>

      {/* Filters */}
      {showFilters && (
        <Card>
          <CardHeader className="py-4">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-muted-foreground">Filters</span>
              {hasActiveFilters && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => resetAll()}
                >
                  <X className="h-4 w-4 mr-1" />
                  Clear
                </Button>
              )}
            </div>
          </CardHeader>
          <CardContent className="pt-0">
            <div className="grid gap-4 md:grid-cols-6">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">
                  Status
                </label>
                <Select value={statusFilter} onValueChange={handleFilterChange}>
                  <SelectTrigger>
                    <SelectValue placeholder="All Statuses" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All</SelectItem>
                    <SelectItem value="active">Active</SelectItem>
                    <SelectItem value="completed">Completed</SelectItem>
                    <SelectItem value="error">Error</SelectItem>
                    <SelectItem value="interrupted">Interrupted</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">
                  Sort
                </label>
                <Select value={sort} onValueChange={handleSortChange}>
                  <SelectTrigger>
                    <SelectValue placeholder="Newest first" />
                  </SelectTrigger>
                  <SelectContent>
                    {SORT_OPTIONS.map((option) => (
                      <SelectItem key={option.value} value={option.value}>
                        {option.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">
                  Rows per page
                </label>
                <Select value={String(limit)} onValueChange={handleLimitChange}>
                  <SelectTrigger>
                    <SelectValue placeholder={String(DEFAULT_PAGE_SIZE)} />
                  </SelectTrigger>
                  <SelectContent>
                    {PAGE_SIZE_OPTIONS.map((size) => (
                      <SelectItem key={size} value={String(size)}>
                        {size}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {statusError && (
        <div className="p-4 bg-destructive/10 text-destructive rounded-md">
          Failed to load realtime status
        </div>
      )}

      {/* Status Cards */}
      <div className="grid gap-4 md:grid-cols-4">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <span className="text-sm font-medium text-muted-foreground">Status</span>
            <Radio className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            {statusLoading ? (
              <Skeleton className="h-8 w-24" />
            ) : (
              <div className="flex items-center gap-2">
                <StatusDot status={statusData?.status ?? 'unavailable'} />
                <span className="text-2xl font-bold capitalize">
                  {statusData?.status?.replace('_', ' ') ?? 'Unknown'}
                </span>
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <span className="text-sm font-medium text-muted-foreground">Active Sessions</span>
          </CardHeader>
          <CardContent>
            {statusLoading ? (
              <Skeleton className="h-8 w-20" />
            ) : (
              <>
                <div className="text-2xl font-bold">
                  {statusData?.active_sessions ?? 0} / {statusData?.total_capacity ?? 0}
                </div>
                <p className="text-xs text-muted-foreground">
                  {statusData?.available_capacity ?? 0} available
                </p>
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <span className="text-sm font-medium text-muted-foreground">Workers</span>
          </CardHeader>
          <CardContent>
            {statusLoading ? (
              <Skeleton className="h-8 w-16" />
            ) : (
              <>
                <div className="text-2xl font-bold">
                  {statusData?.ready_workers ?? 0} / {statusData?.worker_count ?? 0}
                </div>
                <p className="text-xs text-muted-foreground">ready</p>
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <span className="text-sm font-medium text-muted-foreground">Capacity Overview</span>
          </CardHeader>
          <CardContent>
            {statusLoading ? (
              <Skeleton className="h-4 w-full" />
            ) : (
              <div className="space-y-2">
                <div className="flex justify-between text-sm">
                  <span>Used</span>
                  <span>{statusData?.active_sessions ?? 0} / {statusData?.total_capacity ?? 0}</span>
                </div>
                <div className="h-2 bg-muted rounded-full overflow-hidden">
                  <div
                    className="h-full bg-primary transition-all"
                    style={{
                      width: `${statusData?.total_capacity ? (statusData.active_sessions / statusData.total_capacity) * 100 : 0}%`,
                    }}
                  />
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {!statusLoading && !statusError && (
        <Card
          className={
            statusGuidance.level === 'error'
              ? 'border-red-500/40 bg-red-500/5'
              : statusGuidance.level === 'warning'
                ? 'border-amber-500/40 bg-amber-500/5'
                : ''
          }
        >
          <CardContent className="py-4 space-y-3">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="space-y-1">
                <p className="text-sm font-medium">{statusGuidance.title}</p>
                <p className="text-sm text-muted-foreground">{statusGuidance.summary}</p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => navigate('/engines')}
                >
                  Check engine health
                </Button>
                {statusGuidance.showStartWorker && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setShowStatusWhy(true)}
                  >
                    Start worker (self-hosted)
                  </Button>
                )}
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setShowStatusWhy((prev) => !prev)}
                >
                  {showStatusWhy ? 'Hide why this state' : 'Why this state?'}
                </Button>
              </div>
            </div>

            {showStatusWhy && (
              <div className="rounded-md border border-border bg-muted/20 p-3 text-sm space-y-2">
                <p className="text-muted-foreground">{statusGuidance.details}</p>
                {statusGuidance.showStartWorker && (
                  <>
                    <p className="text-xs text-muted-foreground">
                      Self-hosted quick start:
                    </p>
                    <code className="block rounded bg-muted px-2 py-1 text-xs font-mono">
                      docker compose up -d stt-rt-transcribe-parakeet-rnnt-0.6b-cpu
                    </code>
                    <p className="text-xs text-muted-foreground">
                      On AWS/ECS/Kubernetes, scale your realtime worker service/deployment instead.
                    </p>
                  </>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* Session History */}
      <Card>
        <CardHeader>
          <CardTitle>Session History</CardTitle>
        </CardHeader>
        <CardContent>
          {(sessionsLoading || (isFetching && allSessions.length === 0)) ? (
            <div className="space-y-2">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : visibleSessions.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              No sessions found
            </div>
          ) : (
            isMobile ? (
              <div className="space-y-3">
                {visibleSessions.map((session) => (
                  <div
                    key={session.id}
                    className="rounded-lg border border-border p-3 cursor-pointer hover:bg-accent/50"
                    onClick={() => navigate(`/realtime/sessions/${session.id}`)}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="font-mono text-xs break-all">{session.id}</p>
                        <p className="text-xs text-muted-foreground mt-1">
                          {formatDate(session.started_at)}
                        </p>
                      </div>
                      <StatusBadge status={session.status} />
                    </div>
                    <div className="mt-3 grid grid-cols-2 gap-3 text-sm">
                      <div>
                        <p className="text-xs text-muted-foreground">Model</p>
                        <p>{session.model ?? '-'}</p>
                        {session.engine && (
                          <p className="text-xs text-muted-foreground">{session.engine}</p>
                        )}
                      </div>
                      <div>
                        <p className="text-xs text-muted-foreground">Duration</p>
                        <p>{formatDuration(session.audio_duration_seconds)}</p>
                      </div>
                      <div>
                        <p className="text-xs text-muted-foreground">Segments</p>
                        <p>{session.segment_count}</p>
                      </div>
                      <div>
                        <p className="text-xs text-muted-foreground">Storage</p>
                        <div className="flex items-center gap-2 mt-1">
                          {session.store_audio && (
                            <span title="Audio stored">
                              <Mic className="h-3 w-3 text-green-500" />
                            </span>
                          )}
                          {session.store_transcript && (
                            <span title="Transcript stored">
                              <MessageSquare className="h-3 w-3 text-blue-500" />
                            </span>
                          )}
                          {!session.store_audio && !session.store_transcript && (
                            <span className="text-muted-foreground">-</span>
                          )}
                        </div>
                      </div>
                    </div>
                    {session.status !== 'active' && (
                      <div className="mt-3 flex justify-end">
                        <Button
                          variant="outline"
                          size="sm"
                          className="text-red-400 hover:text-red-300 hover:bg-red-950"
                          onClick={(e) => {
                            e.stopPropagation()
                            setDeleteTarget({ id: session.id })
                          }}
                        >
                          <Trash2 className="h-4 w-4 mr-1" />
                          Delete
                        </Button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <Table className="min-w-[900px]">
                <TableHeader>
                  <TableRow>
                    <TableHead className="sticky left-0 z-10 bg-card">Session ID</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Model</TableHead>
                    <TableHead>Duration</TableHead>
                    <TableHead>Segments</TableHead>
                    <TableHead>Storage</TableHead>
                    <TableHead>Started</TableHead>
                    <TableHead className="sticky right-0 z-10 bg-card"></TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {visibleSessions.map((session) => (
                    <TableRow
                      key={session.id}
                      className="cursor-pointer hover:bg-accent/50"
                      onClick={() => navigate(`/realtime/sessions/${session.id}`)}
                    >
                      <TableCell className="font-mono text-sm sticky left-0 z-10 bg-card">
                        {session.id.slice(0, 12)}...
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={session.status} />
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        <div className="flex flex-col">
                          <span>{session.model ?? '-'}</span>
                          {session.engine && (
                            <span className="text-xs text-muted-foreground">{session.engine}</span>
                          )}
                        </div>
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        {formatDuration(session.audio_duration_seconds)}
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        {session.segment_count}
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        <div className="flex items-center gap-2">
                          {session.store_audio && (
                            <span title="Audio stored">
                              <Mic className="h-3 w-3 text-green-500" />
                            </span>
                          )}
                          {session.store_transcript && (
                            <span title="Transcript stored">
                              <MessageSquare className="h-3 w-3 text-blue-500" />
                            </span>
                          )}
                          {!session.store_audio && !session.store_transcript && (
                            <span className="text-muted-foreground">-</span>
                          )}
                        </div>
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        {formatDate(session.started_at)}
                      </TableCell>
                      <TableCell className="text-right sticky right-0 z-10 bg-card">
                        {session.status !== 'active' && (
                          <Button
                            variant="ghost"
                            size="sm"
                            className="text-red-400 hover:text-red-300 hover:bg-red-950"
                            onClick={(e) => {
                              e.stopPropagation()
                              setDeleteTarget({ id: session.id })
                            }}
                            title="Delete session"
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )
          )}
          <ListLoadMoreFooter
            count={visibleSessions.length}
            itemLabel="sessions"
            hasNextPage={hasNextPage}
            isFetchingNextPage={isFetchingNextPage}
            onLoadMore={loadMore}
          />
        </CardContent>
      </Card>

      {/* Delete Confirmation Dialog */}
      <Dialog open={deleteTarget !== null} onOpenChange={(open) => { if (!open) { setDeleteTarget(null); setDeleteError(null) } }}>
        <DialogContent>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Delete Session</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-muted-foreground">
                This will permanently delete the session and all its stored data
                (audio, transcripts). This action cannot be undone.
              </p>
              {deleteTarget && (
                <p className="text-sm font-mono text-muted-foreground">
                  {deleteTarget.id}
                </p>
              )}
              {deleteError && (
                <p className="text-sm text-red-400">{deleteError}</p>
              )}
              <div className="flex justify-end gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => { setDeleteTarget(null); setDeleteError(null) }}
                  disabled={isDeleting}
                >
                  Cancel
                </Button>
                <Button
                  variant="default"
                  size="sm"
                  className="bg-red-600 hover:bg-red-700"
                  onClick={handleDelete}
                  disabled={isDeleting}
                >
                  {isDeleting ? 'Deleting...' : 'Delete'}
                </Button>
              </div>
            </CardContent>
          </Card>
        </DialogContent>
      </Dialog>
    </div>
  )
}
