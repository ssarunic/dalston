import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Radio, MessageSquare, Mic, Trash2 } from 'lucide-react'
import { apiClient } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
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
import type { RealtimeSessionStatus } from '@/api/types'

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

export function RealtimeSessions() {
  const navigate = useNavigate()
  const [statusFilter, setStatusFilter] = useState<string>('all')
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const { data: statusData, isLoading: statusLoading, error: statusError } = useRealtimeStatus()
  const { data: sessionsData, isLoading: sessionsLoading, refetch } = useRealtimeSessions({
    status: statusFilter === 'all' ? undefined : statusFilter,
    limit: 50,
  })

  const handleDelete = async (sessionId: string) => {
    if (!confirm('Are you sure you want to delete this session?')) {
      return
    }
    setDeletingId(sessionId)
    try {
      await apiClient.deleteRealtimeSession(sessionId)
      refetch()
    } catch (error) {
      console.error('Failed to delete session:', error)
      alert('Failed to delete session')
    } finally {
      setDeletingId(null)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Realtime</h1>
        <p className="text-muted-foreground">
          Real-time transcription workers, capacity, and session history
        </p>
      </div>

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
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>Session History</CardTitle>
          <Select value={statusFilter} onValueChange={setStatusFilter}>
            <SelectTrigger className="w-[150px]">
              <SelectValue placeholder="Filter status" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All</SelectItem>
              <SelectItem value="active">Active</SelectItem>
              <SelectItem value="completed">Completed</SelectItem>
              <SelectItem value="error">Error</SelectItem>
              <SelectItem value="interrupted">Interrupted</SelectItem>
            </SelectContent>
          </Select>
        </CardHeader>
        <CardContent>
          {sessionsLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : sessionsData?.sessions.length === 0 ? (
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
                {sessionsData?.sessions.map((session) => (
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
                            handleDelete(session.id)
                          }}
                          disabled={deletingId === session.id}
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
          {sessionsData && sessionsData.total > sessionsData.sessions.length && (
            <div className="mt-4 text-center text-sm text-muted-foreground">
              Showing {sessionsData.sessions.length} of {sessionsData.total} sessions
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
