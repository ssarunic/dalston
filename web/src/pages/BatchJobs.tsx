import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronLeft, ChevronRight, Trash2, X } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { useJobs } from '@/hooks/useJobs'
import { apiClient } from '@/api/client'
import type { JobStatus } from '@/api/types'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { StatusBadge } from '@/components/StatusBadge'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'

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

export function BatchJobs() {
  const [statusFilter, setStatusFilter] = useState<string>('')
  const [page, setPage] = useState(0)
  const [deleteTarget, setDeleteTarget] = useState<{ id: string } | null>(null)
  const [isDeleting, setIsDeleting] = useState(false)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [cancelTarget, setCancelTarget] = useState<{ id: string } | null>(null)
  const [isCancelling, setIsCancelling] = useState(false)
  const [cancelError, setCancelError] = useState<string | null>(null)
  const [cancelSuccess, setCancelSuccess] = useState<string | null>(null)
  const queryClient = useQueryClient()

  const { data, isLoading, error } = useJobs({
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
    status: statusFilter || undefined,
  })

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 0

  async function handleDelete() {
    if (!deleteTarget) return
    setIsDeleting(true)
    setDeleteError(null)
    try {
      await apiClient.deleteJob(deleteTarget.id)
      setDeleteTarget(null)
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
      <div>
        <h1 className="text-2xl font-bold">Batch Jobs</h1>
        <p className="text-muted-foreground">
          Manage and monitor transcription jobs
        </p>
      </div>

      {/* Filters */}
      <div className="flex gap-2">
        {STATUS_FILTERS.map((filter) => (
          <Button
            key={filter.value}
            variant={statusFilter === filter.value ? 'default' : 'outline'}
            size="sm"
            onClick={() => {
              setStatusFilter(filter.value)
              setPage(0)
            }}
          >
            {filter.label}
          </Button>
        ))}
      </div>

      {/* Jobs Table */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base font-medium">
            {data?.total ?? 0} jobs
          </CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-3">
              {[...Array(5)].map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : error ? (
            <p className="text-red-400 py-4">Error loading jobs</p>
          ) : data?.jobs.length === 0 ? (
            <p className="text-muted-foreground py-8 text-center">
              No jobs found
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Job ID</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>State</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data?.jobs.map((job) => (
                  <TableRow key={job.id}>
                    <TableCell>
                      <Link
                        to={`/jobs/${job.id}`}
                        className="font-mono text-sm hover:text-primary"
                      >
                        {job.id.slice(0, 12)}...
                      </Link>
                    </TableCell>
                    <TableCell>
                      <StatusBadge status={job.status} />
                    </TableCell>
                    <TableCell>
                      {job.status === 'running' ? (
                        <span className="text-xs text-muted-foreground">In progress</span>
                      ) : job.status === 'completed' ? (
                        <span className="text-xs text-muted-foreground">Done</span>
                      ) : (
                        <span className="text-xs text-muted-foreground">-</span>
                      )}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-sm">
                      {formatDate(job.created_at)}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex items-center justify-end gap-1">
                        <Link to={`/jobs/${job.id}`}>
                          <Button variant="ghost" size="sm">
                            View
                          </Button>
                        </Link>
                        <div className="w-8">
                          {CANCELLABLE_STATUSES.has(job.status as JobStatus) && (
                            <Button
                              variant="ghost"
                              size="sm"
                              className="text-amber-400 hover:text-amber-300 hover:bg-amber-950"
                              onClick={() => setCancelTarget({ id: job.id })}
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
                              onClick={() => setDeleteTarget({ id: job.id })}
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
          {totalPages > 1 && (
            <div className="flex items-center justify-between pt-4">
              <p className="text-sm text-muted-foreground">
                Page {page + 1} of {totalPages}
              </p>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPage((p) => Math.max(0, p - 1))}
                  disabled={page === 0}
                >
                  <ChevronLeft className="h-4 w-4" />
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                  disabled={page >= totalPages - 1}
                >
                  Next
                  <ChevronRight className="h-4 w-4" />
                </Button>
              </div>
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
