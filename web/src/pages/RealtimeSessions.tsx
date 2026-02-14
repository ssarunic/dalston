import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { Radio, MessageSquare, Mic, Trash2, RefreshCw, Filter, X } from 'lucide-react'
import { apiClient } from '@/api/client'
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
import { useRealtimeStatus } from '@/hooks/useRealtimeStatus'
import { useRealtimeSessions } from '@/hooks/useRealtimeSessions'
import type { RealtimeSessionStatus, RealtimeSessionSummary } from '@/api/types'

function StatusDot({ status }: { status: string }) {
  const color =
    status === 'ready'
      ? 'bg-green-500'
      : status === 'at_capacity'
        ? 'bg-yellow-500'
        : 'bg-red-500'
  return <span className={`inline-block w-3 h-3 rounded-full ${color}`} />
}

function SessionStatusBadge({ status }: { status: RealtimeSessionStatus }) {
  const variants: Record<RealtimeSessionStatus, 'default' | 'secondary' | 'destructive' | 'outline'> = {
    active: 'default',
    completed: 'secondary',
    error: 'destructive',
    interrupted: 'outline',
  }
  return <Badge variant={variants[status]}>{status}</Badge>
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

const PAGE_SIZE = 50

export function RealtimeSessions() {
  const navigate = useNavigate()
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [deleteTarget, setDeleteTarget] = useState<{ id: string } | null>(null)
  const [isDeleting, setIsDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [cursor, setCursor] = useState<string | undefined>(undefined)
  const [allSessions, setAllSessions] = useState<RealtimeSessionSummary[]>([])
  const [showFilters, setShowFilters] = useState(false)

  const hasActiveFilters = statusFilter !== 'all'
  const { data: statusData, isLoading: statusLoading, error: statusError } = useRealtimeStatus()
  const { data: sessionsData, isLoading: sessionsLoading, isFetching, refetch } = useRealtimeSessions({
    status: statusFilter === 'all' ? undefined : statusFilter,
    limit: PAGE_SIZE,
    cursor,
  })

  // Accumulate sessions when data changes (intentional pattern for cursor pagination)
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    if (sessionsData?.sessions) {
      if (cursor === undefined) {
        setAllSessions(() => sessionsData.sessions)
      } else {
        setAllSessions((prev) => [...prev, ...sessionsData.sessions])
      }
    }
  }, [sessionsData, cursor])
  /* eslint-enable react-hooks/set-state-in-effect */

  // Reset pagination when filter changes
  const handleFilterChange = useCallback((value: string) => {
    setStatusFilter(value)
    setCursor(undefined)
    setAllSessions([])
  }, [])

  const loadMore = () => {
    if (sessionsData?.has_more && sessionsData?.cursor) {
      setCursor(sessionsData.cursor)
    }
  }

  const handleRefresh = async () => {
    setCursor(undefined)
    setAllSessions([])
    const { data: newData } = await refetch()
    if (newData?.sessions) {
      setAllSessions(newData.sessions)
    }
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    setIsDeleting(true)
    setDeleteError(null)
    try {
      await apiClient.deleteRealtimeSession(deleteTarget.id)
      setDeleteTarget(null)
      // Reset and refetch to get fresh data
      setCursor(undefined)
      setAllSessions([])
      refetch()
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
              <CardTitle className="text-sm font-medium">Filters</CardTitle>
              {hasActiveFilters && (
                <Button variant="ghost" size="sm" onClick={() => handleFilterChange('all')}>
                  <X className="h-4 w-4 mr-1" />
                  Clear
                </Button>
              )}
            </div>
          </CardHeader>
          <CardContent className="pt-0">
            <div className="grid gap-4 md:grid-cols-4">
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
            <CardTitle className="text-sm font-medium">Status</CardTitle>
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
            <CardTitle className="text-sm font-medium">Active Sessions</CardTitle>
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
            <CardTitle className="text-sm font-medium">Workers</CardTitle>
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
            <CardTitle className="text-sm font-medium">Capacity Overview</CardTitle>
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

      {/* Session History */}
      <Card>
        <CardHeader>
          <CardTitle>Session History</CardTitle>
        </CardHeader>
        <CardContent>
          {(sessionsLoading || (isFetching && allSessions.length === 0)) && cursor === undefined ? (
            <div className="space-y-2">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : allSessions.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              No sessions found
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Session ID</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Model</TableHead>
                  <TableHead>Duration</TableHead>
                  <TableHead>Segments</TableHead>
                  <TableHead>Storage</TableHead>
                  <TableHead>Started</TableHead>
                  <TableHead></TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {allSessions.map((session) => (
                  <TableRow
                    key={session.id}
                    className="cursor-pointer hover:bg-accent/50"
                    onClick={() => navigate(`/realtime/sessions/${session.id}`)}
                  >
                    <TableCell className="font-mono text-sm">
                      {session.id.slice(0, 12)}...
                    </TableCell>
                    <TableCell>
                      <SessionStatusBadge status={session.status} />
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
                    <TableCell className="text-right">
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
          )}
          {allSessions.length > 0 && (
            <div className="flex flex-col items-center gap-3 pt-4">
              <p className="text-sm text-muted-foreground">
                Showing {allSessions.length} sessions
              </p>
              {sessionsData?.has_more && (
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
