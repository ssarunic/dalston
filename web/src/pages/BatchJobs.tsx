import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { useJobs } from '@/hooks/useJobs'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { StatusBadge } from '@/components/StatusBadge'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import type { JobStatus } from '@/api/types'

const PAGE_SIZE = 20

const STATUS_FILTERS: { label: string; value: JobStatus | '' }[] = [
  { label: 'All', value: '' },
  { label: 'Pending', value: 'pending' },
  { label: 'Running', value: 'running' },
  { label: 'Completed', value: 'completed' },
  { label: 'Failed', value: 'failed' },
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

  const { data, isLoading, error } = useJobs({
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
    status: statusFilter || undefined,
  })

  const totalPages = data ? Math.ceil(data.total / PAGE_SIZE) : 0

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
                  <TableHead>Progress</TableHead>
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
                      {job.status === 'running' && job.progress !== undefined ? (
                        <div className="flex items-center gap-2">
                          <div className="h-2 w-24 rounded-full bg-muted overflow-hidden">
                            <div
                              className="h-full bg-primary transition-all"
                              style={{ width: `${job.progress}%` }}
                            />
                          </div>
                          <span className="text-xs text-muted-foreground">
                            {job.progress}%
                          </span>
                        </div>
                      ) : job.status === 'completed' ? (
                        <span className="text-xs text-muted-foreground">100%</span>
                      ) : (
                        <span className="text-xs text-muted-foreground">-</span>
                      )}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-sm">
                      {formatDate(job.created_at)}
                    </TableCell>
                    <TableCell className="text-right">
                      <Link to={`/jobs/${job.id}`}>
                        <Button variant="ghost" size="sm">
                          View
                        </Button>
                      </Link>
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
    </div>
  )
}
