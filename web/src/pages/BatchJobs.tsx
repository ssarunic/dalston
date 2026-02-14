import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { Trash2, X, RefreshCw, Filter } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { useJobs } from '@/hooks/useJobs'
import { apiClient } from '@/api/client'
import type { JobStatus, ConsoleJobSummary } from '@/api/types'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { StatusBadge } from '@/components/StatusBadge'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { Badge } from '@/components/ui/badge'
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

const PAGE_SIZE = 20

const TERMINAL_STATUSES: Set<JobStatus> = new Set(['completed', 'failed', 'cancelled'])
const CANCELLABLE_STATUSES: Set<JobStatus> = new Set(['pending', 'running'])

const STATUS_FILTERS: { label: string; value: JobStatus | '' }[] = [
  { label: 'All', value: '' },
  { label: 'Pending', value: 'pending' },
  { label: 'Running', value: 'running' },
  { label: 'Completed', value: 'completed' },
  { label: 'Failed', value: 'failed' },
  { label: 'Cancelled', value: 'cancelled' },
]

function formatDate(dateStr: string): string {
  const date = new Date(dateStr)
  return date.toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatDuration(seconds: number | undefined): string {
  if (seconds === undefined || seconds === null) return '-'
  if (seconds < 60) return `${Math.round(seconds)}s`
  const mins = Math.floor(seconds / 60)
  const secs = Math.round(seconds % 60)
  return `${mins}m ${secs}s`
}

export function BatchJobs() {
  const [statusFilter, setStatusFilter] = useState<string>('')
  const [cursor, setCursor] = useState<string | undefined>(undefined)
  const [allJobs, setAllJobs] = useState<ConsoleJobSummary[]>([])
  const [deleteTarget, setDeleteTarget] = useState<{ id: string } | null>(null)
  const [isDeleting, setIsDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [cancelTarget, setCancelTarget] = useState<{ id: string } | null>(null)
  const [isCancelling, setIsCancelling] = useState(false)
  const [cancelError, setCancelError] = useState<string | null>(null)
  const [cancelSuccess, setCancelSuccess] = useState<string | null>(null)
  const [showFilters, setShowFilters] = useState(false)
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  const hasActiveFilters = !!statusFilter

  const { data, isLoading, isFetching, error, refetch } = useJobs({
    limit: PAGE_SIZE,
    cursor,
    status: statusFilter || undefined,
  })

  // Accumulate jobs when data changes (intentional pattern for cursor pagination)
  useEffect(() => {
    if (data?.jobs) {
      if (cursor === undefined) {
        setAllJobs(() => data.jobs)
      } else {
        setAllJobs((prev) => [...prev, ...data.jobs])
      }
    }
  }, [data, cursor])

  // Reset pagination when filter changes
  const handleFilterChange = useCallback((value: string) => {
    setStatusFilter(value)
    setCursor(undefined)
    setAllJobs([])
  }, [])

  const loadMore = () => {
    if (data?.has_more && data?.cursor) {
      setCursor(data.cursor)
    }
  }

  const handleRefresh = async () => {
    setCursor(undefined)
    setAllJobs([])
    const { data: newData } = await refetch()
    if (newData?.jobs) {
      setAllJobs(newData.jobs)
    }
  }

  async function handleDelete() {
    if (!deleteTarget) return
    setIsDeleting(true)
    setDeleteError(null)
    try {
      await apiClient.deleteJob(deleteTarget.id)
      setDeleteTarget(null)
      // Reset and refetch to get fresh data
      setCursor(undefined)
      setAllJobs([])
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
    } catch (err) {
      const message =
        err instanceof Error ? err.message : 'Failed to delete job'
      setDeleteError(message)
    } finally {
      setIsDeleting(false)
    }
  }

  async function handleCancel() {
    if (!cancelTarget) return
    setIsCancelling(true)
    setCancelError(null)
    try {
      const result = await apiClient.cancelJob(cancelTarget.id)
      setCancelTarget(null)
      setCancelSuccess(result.message)
      // Reset and refetch to get fresh data
      setCursor(undefined)
      setAllJobs([])
      queryClient.invalidateQueries({ queryKey: ['jobs'] })
      // Clear success message after 3 seconds
      setTimeout(() => setCancelSuccess(null), 3000)
    } catch (err) {
      const message =
        err instanceof Error ? err.message : 'Failed to cancel job'
      setCancelError(message)
    } finally {
      setIsCancelling(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Batch Jobs</h1>
          <p className="text-muted-foreground">
            Manage and monitor transcription jobs
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
                <Button variant="ghost" size="sm" onClick={() => handleFilterChange('')}>
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
                <Select
                  value={statusFilter}
                  onValueChange={handleFilterChange}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="All Statuses" />
                  </SelectTrigger>
                  <SelectContent>
                    {STATUS_FILTERS.map((filter) => (
                      <SelectItem key={filter.value} value={filter.value}>
                        {filter.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Jobs Table */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base font-medium">
            Jobs
          </CardTitle>
        </CardHeader>
        <CardContent>
          {(isLoading || (isFetching && allJobs.length === 0)) && cursor === undefined ? (
            <div className="space-y-3">
              {[...Array(5)].map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : error ? (
            <p className="text-red-400 py-4">Error loading jobs</p>
          ) : allJobs.length === 0 ? (
            <p className="text-muted-foreground py-8 text-center">
              No jobs found
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Job ID</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Duration</TableHead>
                  <TableHead>Language</TableHead>
                  <TableHead className="text-right">Words</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {allJobs.map((job) => (
                  <TableRow
                    key={job.id}
                    className="cursor-pointer hover:bg-accent/50"
                    onClick={() => navigate(`/jobs/${job.id}`)}
                  >
                    <TableCell className="font-mono text-sm">
                      {job.id.slice(0, 12)}...
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={job.status} />
                    </TableCell>
                    <TableCell className="text-muted-foreground text-sm">
                      {formatDuration(job.audio_duration_seconds)}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-sm">
                      {job.result_word_count ? job.result_language_code?.toUpperCase() || '-' : '-'}
                    </TableCell>
                    <TableCell className="text-right text-muted-foreground text-sm">
                      {job.result_word_count?.toLocaleString() || '-'}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-sm">
                      {formatDate(job.created_at)}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex items-center justify-end gap-1">
                        <div className="w-8">
                          {CANCELLABLE_STATUSES.has(job.status as JobStatus) && (
                            <Button
                              variant="ghost"
                              size="sm"
                              className="text-amber-400 hover:text-amber-300 hover:bg-amber-950"
                              onClick={(e) => {
                                e.stopPropagation()
                                setCancelTarget({ id: job.id })
                              }}
                              title="Cancel job"
                            >
                              <X className="h-4 w-4" />
                            </Button>
                          )}
                        </div>
                        <div className="w-8">
                          {TERMINAL_STATUSES.has(job.status as JobStatus) && (
                            <Button
                              variant="ghost"
                              size="sm"
                              className="text-red-400 hover:text-red-300 hover:bg-red-950"
                              onClick={(e) => {
                                e.stopPropagation()
                                setDeleteTarget({ id: job.id })
                              }}
                              title="Delete job"
                            >
                              <Trash2 className="h-4 w-4" />
                            </Button>
                          )}
                        </div>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}

          {/* Pagination */}
          {allJobs.length > 0 && (
            <div className="flex flex-col items-center gap-3 pt-4">
              <p className="text-sm text-muted-foreground">
                Showing {allJobs.length} jobs
              </p>
              {data?.has_more && (
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

      {/* Success Toast */}
      {cancelSuccess && (
        <div className="fixed bottom-4 right-4 bg-green-600 text-white px-4 py-2 rounded-md shadow-lg">
          {cancelSuccess}
        </div>
      )}

      {/* Cancel Confirmation Dialog */}
      <Dialog open={cancelTarget !== null} onOpenChange={(open) => { if (!open) { setCancelTarget(null); setCancelError(null) } }}>
        <DialogContent>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Cancel Job</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-muted-foreground">
                This will cancel the job. Running tasks will complete naturally,
                but no new tasks will be started.
              </p>
              {cancelTarget && (
                <p className="text-sm font-mono text-muted-foreground">
                  {cancelTarget.id}
                </p>
              )}
              {cancelError && (
                <p className="text-sm text-red-400">{cancelError}</p>
              )}
              <div className="flex justify-end gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => { setCancelTarget(null); setCancelError(null) }}
                  disabled={isCancelling}
                >
                  Close
                </Button>
                <Button
                  variant="default"
                  size="sm"
                  className="bg-amber-600 hover:bg-amber-700"
                  onClick={handleCancel}
                  disabled={isCancelling}
                >
                  {isCancelling ? 'Cancelling...' : 'Cancel Job'}
                </Button>
              </div>
            </CardContent>
          </Card>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation Dialog */}
      <Dialog open={deleteTarget !== null} onOpenChange={(open) => { if (!open) { setDeleteTarget(null); setDeleteError(null) } }}>
        <DialogContent>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Delete Job</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-muted-foreground">
                This will permanently delete the job and all its artifacts
                (audio, transcripts, intermediate outputs). This action cannot
                be undone.
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
