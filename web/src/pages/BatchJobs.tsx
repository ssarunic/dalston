import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Trash2, X, Plus } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { useJobs } from '@/hooks/useJobs'
import { useMediaQuery } from '@/hooks/useMediaQuery'
import { useSharedTableState } from '@/hooks/useSharedTableState'
import { apiClient } from '@/api/client'
import type { JobStatus } from '@/api/types'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { StatusBadge } from '@/components/StatusBadge'
import { ListLoadMoreFooter } from '@/components/ListLoadMoreFooter'
import { Dialog, DialogContent } from '@/components/ui/dialog'
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

const DEFAULT_PAGE_SIZE = 20
const PAGE_SIZE_OPTIONS = [20, 50, 100] as const
const STATUS_OPTIONS = ['', 'pending', 'running', 'completed', 'failed', 'cancelled'] as const
const SORT_OPTION_VALUES = ['created_desc', 'created_asc'] as const
const SORT_OPTIONS = [
  { label: 'Newest first', value: 'created_desc' },
  { label: 'Oldest first', value: 'created_asc' },
] as const

const TERMINAL_STATUSES: Set<JobStatus> = new Set(['completed', 'failed', 'cancelled'])
const CANCELLABLE_STATUSES: Set<JobStatus> = new Set(['pending', 'running'])

const STATUS_FILTERS: { label: string; value: (typeof STATUS_OPTIONS)[number] }[] = [
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
  const isMobile = useMediaQuery('(max-width: 767px)')
  const {
    status: statusFilter,
    sort,
    limit,
    setStatus,
    setSort,
    setLimit,
  } = useSharedTableState({
    defaultStatus: '',
    statusOptions: STATUS_OPTIONS,
    defaultSort: 'created_desc',
    sortOptions: SORT_OPTION_VALUES,
    defaultLimit: DEFAULT_PAGE_SIZE,
    limitOptions: PAGE_SIZE_OPTIONS,
  })
  const [deleteTarget, setDeleteTarget] = useState<{ id: string } | null>(null)
  const [isDeleting, setIsDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [cancelTarget, setCancelTarget] = useState<{ id: string } | null>(null)
  const [isCancelling, setIsCancelling] = useState(false)
  const [cancelError, setCancelError] = useState<string | null>(null)
  const [cancelSuccess, setCancelSuccess] = useState<string | null>(null)
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  const {
    data,
    isLoading,
    isFetchingNextPage,
    hasNextPage,
    fetchNextPage,
    error,
  } = useJobs({
    limit,
    status: statusFilter || undefined,
    sort,
  })
  const allJobs = useMemo(() => data?.pages.flatMap((page) => page.jobs) ?? [], [data])
  const visibleJobs = allJobs

  const handleFilterChange = (value: string) => {
    setStatus(value)
  }

  const handleSortChange = (value: string) => {
    setSort(value)
  }

  const handleLimitChange = (value: string) => {
    setLimit(Number(value))
  }

  const loadMore = () => {
    if (!hasNextPage || isFetchingNextPage) return
    void fetchNextPage()
  }

  async function handleDelete() {
    if (!deleteTarget) return
    setIsDeleting(true)
    setDeleteError(null)
    try {
      await apiClient.deleteJob(deleteTarget.id)
      setDeleteTarget(null)
      await queryClient.invalidateQueries({ queryKey: ['jobs'] })
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
      await queryClient.invalidateQueries({ queryKey: ['jobs'] })
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
        <Button onClick={() => navigate('/jobs/new')}>
          <Plus className="h-4 w-4 mr-2" />
          Submit Job
        </Button>
      </div>

      {/* Jobs Table */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="text-base font-medium">
            Jobs
          </CardTitle>
          <div className="flex items-center gap-2">
            <Select
              value={statusFilter}
              onValueChange={handleFilterChange}
            >
              <SelectTrigger className="w-[130px]">
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
            <Select value={sort} onValueChange={handleSortChange}>
              <SelectTrigger className="w-[150px]">
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
            <Select value={String(limit)} onValueChange={handleLimitChange}>
              <SelectTrigger className="w-[100px]">
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
        </CardHeader>
        <CardContent>
          {isLoading && allJobs.length === 0 ? (
            <div className="space-y-3">
              {[...Array(5)].map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : error ? (
            <p className="text-red-400 py-4">Error loading jobs</p>
          ) : visibleJobs.length === 0 ? (
            <div className="text-center py-12">
              <p className="text-muted-foreground mb-4">
                {statusFilter ? 'No jobs found matching the filter' : 'No jobs yet'}
              </p>
              {!statusFilter && (
                <Button variant="outline" onClick={() => navigate('/jobs/new')}>
                  <Plus className="h-4 w-4 mr-2" />
                  Submit your first job
                </Button>
              )}
            </div>
          ) : (
            isMobile ? (
              <div className="space-y-3">
                {visibleJobs.map((job) => (
                  <div
                    key={job.id}
                    className="rounded-lg border border-border p-3 cursor-pointer hover:bg-accent/50"
                    onClick={() => navigate(`/jobs/${job.id}`)}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="font-mono text-xs break-all">{job.id}</p>
                        <p className="text-xs text-muted-foreground mt-1">
                          {formatDate(job.created_at)}
                        </p>
                      </div>
                      <StatusBadge status={job.status} />
                    </div>
                    <div className="mt-3 grid grid-cols-3 gap-3 text-sm">
                      <div>
                        <p className="text-xs text-muted-foreground">Model</p>
                        <p>{job.model ?? '-'}</p>
                      </div>
                      <div>
                        <p className="text-xs text-muted-foreground">Duration</p>
                        <p>{formatDuration(job.audio_duration_seconds)}</p>
                      </div>
                      <div>
                        <p className="text-xs text-muted-foreground">Segments</p>
                        <p>{job.result_segment_count ?? '-'}</p>
                      </div>
                    </div>
                    {(CANCELLABLE_STATUSES.has(job.status as JobStatus) ||
                      TERMINAL_STATUSES.has(job.status as JobStatus)) && (
                      <div className="mt-3 flex justify-end gap-2">
                        {CANCELLABLE_STATUSES.has(job.status as JobStatus) && (
                          <Button
                            variant="outline"
                            size="sm"
                            className="text-amber-400 hover:text-amber-300 hover:bg-amber-950"
                            onClick={(e) => {
                              e.stopPropagation()
                              setCancelTarget({ id: job.id })
                            }}
                          >
                            <X className="h-4 w-4 mr-1" />
                            Cancel
                          </Button>
                        )}
                        {TERMINAL_STATUSES.has(job.status as JobStatus) && (
                          <Button
                            variant="outline"
                            size="sm"
                            className="text-red-400 hover:text-red-300 hover:bg-red-950"
                            onClick={(e) => {
                              e.stopPropagation()
                              setDeleteTarget({ id: job.id })
                            }}
                          >
                            <Trash2 className="h-4 w-4 mr-1" />
                            Delete
                          </Button>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <Table className="min-w-[860px]">
                <TableHeader>
                  <TableRow>
                    <TableHead className="sticky left-0 z-10 bg-card">ID</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead>Model</TableHead>
                    <TableHead>Duration</TableHead>
                    <TableHead>Segments</TableHead>
                    <TableHead>Created</TableHead>
                    <TableHead className="sticky right-0 z-10 bg-card text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {visibleJobs.map((job) => (
                    <TableRow
                      key={job.id}
                      className="group cursor-pointer hover:bg-accent/50"
                      onClick={() => navigate(`/jobs/${job.id}`)}
                    >
                      <TableCell className="font-mono text-sm sticky left-0 z-10 bg-card group-hover:bg-accent/50">
                        {job.id.slice(0, 12)}...
                      </TableCell>
                      <TableCell>
                        <StatusBadge status={job.status} />
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        {job.model ?? '-'}
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        {formatDuration(job.audio_duration_seconds)}
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        {job.result_segment_count ?? '-'}
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        {formatDate(job.created_at)}
                      </TableCell>
                      <TableCell className="text-right sticky right-0 z-10 bg-card group-hover:bg-accent/50">
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
            )
          )}

          <ListLoadMoreFooter
            count={visibleJobs.length}
            itemLabel="jobs"
            hasNextPage={hasNextPage}
            isFetchingNextPage={isFetchingNextPage}
            onLoadMore={loadMore}
          />
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
