import { useState, useRef } from 'react'
import { Link } from 'react-router-dom'
import {
  ScrollText,
  AlertCircle,
  ChevronDown,
  ChevronRight,
  Filter,
  X,
  RefreshCw,
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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useAuditEvents } from '@/hooks/useAuditLog'
import type { AuditEvent, AuditListParams } from '@/api/types'

const RESOURCE_TYPES = [
  { value: '', label: 'All Resources' },
  { value: 'job', label: 'Job' },
  { value: 'transcript', label: 'Transcript' },
  { value: 'audio', label: 'Audio' },
  { value: 'session', label: 'Session' },
  { value: 'api_key', label: 'API Key' },
  { value: 'retention_policy', label: 'Retention Policy' },
]

const ACTION_CATEGORIES: Record<string, { label: string; color: string }> = {
  created: { label: 'Created', color: 'bg-green-500/10 text-green-500 border-green-500/20' },
  completed: { label: 'Completed', color: 'bg-blue-500/10 text-blue-500 border-blue-500/20' },
  accessed: { label: 'Accessed', color: 'bg-slate-500/10 text-slate-400 border-slate-500/20' },
  exported: { label: 'Exported', color: 'bg-purple-500/10 text-purple-500 border-purple-500/20' },
  deleted: { label: 'Deleted', color: 'bg-red-500/10 text-red-500 border-red-500/20' },
  purged: { label: 'Purged', color: 'bg-orange-500/10 text-orange-500 border-orange-500/20' },
  failed: { label: 'Failed', color: 'bg-red-500/10 text-red-500 border-red-500/20' },
  started: { label: 'Started', color: 'bg-cyan-500/10 text-cyan-500 border-cyan-500/20' },
  ended: { label: 'Ended', color: 'bg-slate-500/10 text-slate-400 border-slate-500/20' },
  revoked: { label: 'Revoked', color: 'bg-red-500/10 text-red-500 border-red-500/20' },
  cancelled: { label: 'Cancelled', color: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20' },
}

function getActionStyle(action: string): { label: string; color: string } {
  const actionPart = action.split('.').pop() || action
  return ACTION_CATEGORIES[actionPart] || { label: action, color: 'bg-slate-500/10 text-slate-400 border-slate-500/20' }
}

function formatTimestamp(timestamp: string): string {
  return new Date(timestamp).toLocaleString()
}

function getResourceLink(resourceType: string, resourceId: string): string | null {
  switch (resourceType) {
    case 'job':
      return `/jobs/${resourceId}`
    case 'session':
      return `/realtime/sessions/${resourceId}`
    default:
      return null
  }
}

function EventDetailRow({ event }: { event: AuditEvent }) {
  const [expanded, setExpanded] = useState(false)
  const actionStyle = getActionStyle(event.action)
  const resourceLink = getResourceLink(event.resource_type, event.resource_id)

  return (
    <>
      <TableRow
        className="cursor-pointer hover:bg-muted/50"
        onClick={() => setExpanded(!expanded)}
      >
        <TableCell className="w-8">
          {event.detail ? (
            expanded ? (
              <ChevronDown className="h-4 w-4 text-muted-foreground" />
            ) : (
              <ChevronRight className="h-4 w-4 text-muted-foreground" />
            )
          ) : (
            <span className="w-4" />
          )}
        </TableCell>
        <TableCell className="text-muted-foreground text-sm whitespace-nowrap">
          {formatTimestamp(event.timestamp)}
        </TableCell>
        <TableCell>
          <Badge variant="outline" className={actionStyle.color}>
            {event.action}
          </Badge>
        </TableCell>
        <TableCell>
          <div className="flex items-center gap-2">
            <span className="text-muted-foreground">{event.resource_type}/</span>
            {resourceLink ? (
              <Link
                to={resourceLink}
                className="font-mono text-sm hover:underline"
                onClick={(e) => e.stopPropagation()}
              >
                {event.resource_id.slice(0, 8)}...
              </Link>
            ) : (
              <span className="font-mono text-sm">{event.resource_id.slice(0, 8)}...</span>
            )}
          </div>
        </TableCell>
        <TableCell className="font-mono text-sm">
          {event.actor_id.length > 16 ? `${event.actor_id.slice(0, 16)}...` : event.actor_id}
        </TableCell>
        <TableCell className="text-muted-foreground text-sm">
          {event.ip_address || '-'}
        </TableCell>
      </TableRow>
      {expanded && event.detail && (
        <TableRow>
          <TableCell colSpan={6} className="bg-muted/30">
            <pre className="text-xs font-mono p-3 overflow-x-auto">
              {JSON.stringify(event.detail, null, 2)}
            </pre>
          </TableCell>
        </TableRow>
      )}
    </>
  )
}

export function AuditLog() {
  const [filters, setFilters] = useState<AuditListParams>({
    limit: 50,
  })
  const [showFilters, setShowFilters] = useState(false)
  const sinceRef = useRef<HTMLInputElement>(null)
  const untilRef = useRef<HTMLInputElement>(null)

  const { data, isLoading, error, isFetching } = useAuditEvents(filters)

  const applyDateFilters = () => {
    const sinceValue = sinceRef.current?.value || ''
    const untilValue = untilRef.current?.value || ''
    setFilters((prev) => ({
      ...prev,
      since: sinceValue ? new Date(sinceValue).toISOString() : undefined,
      until: untilValue ? new Date(untilValue).toISOString() : undefined,
      cursor: undefined,
    }))
  }

  const handleFilterChange = (key: keyof AuditListParams, value: string) => {
    setFilters((prev) => ({
      ...prev,
      [key]: value || undefined,
      cursor: undefined,
    }))
  }

  const clearFilters = () => {
    setFilters({ limit: 50 })
  }

  const hasActiveFilters = !!(
    filters.resource_type ||
    filters.action ||
    filters.actor_id ||
    filters.since ||
    filters.until
  )

  const loadMore = () => {
    if (data?.cursor) {
      setFilters((prev) => ({ ...prev, cursor: data.cursor! }))
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Audit Log</h1>
          <p className="text-muted-foreground">View data access and lifecycle events</p>
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
            onClick={() => applyDateFilters()}
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
                <Button variant="ghost" size="sm" onClick={clearFilters}>
                  <X className="h-4 w-4 mr-1" />
                  Clear
                </Button>
              )}
            </div>
          </CardHeader>
          <CardContent className="pt-0">
            <div className="grid gap-4 md:grid-cols-5">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">
                  Resource Type
                </label>
                <Select
                  value={filters.resource_type || ''}
                  onValueChange={(v) => handleFilterChange('resource_type', v)}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="All Resources" />
                  </SelectTrigger>
                  <SelectContent>
                    {RESOURCE_TYPES.map((type) => (
                      <SelectItem key={type.value} value={type.value}>
                        {type.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">
                  Action
                </label>
                <input
                  type="text"
                  placeholder="e.g., job.created"
                  value={filters.action || ''}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => handleFilterChange('action', e.target.value)}
                  className="w-full px-3 py-2 rounded-md border border-input bg-background text-sm h-10"
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">
                  Actor ID
                </label>
                <input
                  type="text"
                  placeholder="e.g., dk_abc1234"
                  value={filters.actor_id || ''}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) => handleFilterChange('actor_id', e.target.value)}
                  className="w-full px-3 py-2 rounded-md border border-input bg-background text-sm h-10"
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">
                  Since
                </label>
                <input
                  ref={sinceRef}
                  type="datetime-local"
                  defaultValue={filters.since?.slice(0, 16) || ''}
                  key={`since-${filters.since || 'empty'}`}
                  className="w-full px-3 py-2 rounded-md border border-input bg-background text-sm h-10 dark:[color-scheme:dark]"
                />
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">
                  Until
                </label>
                <input
                  ref={untilRef}
                  type="datetime-local"
                  defaultValue={filters.until?.slice(0, 16) || ''}
                  key={`until-${filters.until || 'empty'}`}
                  className="w-full px-3 py-2 rounded-md border border-input bg-background text-sm h-10 dark:[color-scheme:dark]"
                />
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Events Table */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ScrollText className="h-5 w-5" />
            Events
          </CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading || isFetching ? (
            <div className="space-y-3">
              {[...Array(10)].map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : error ? (
            <div className="flex items-center gap-2 text-destructive py-8 justify-center">
              <AlertCircle className="h-5 w-5" />
              <span>
                Failed to load audit events:{' '}
                {error instanceof Error ? error.message : 'Unknown error'}
              </span>
            </div>
          ) : !data?.events.length ? (
            <div className="text-center py-12 text-muted-foreground">
              <ScrollText className="h-12 w-12 mx-auto mb-4 opacity-50" />
              <p>No audit events found</p>
              {hasActiveFilters && (
                <p className="text-sm mt-1">Try adjusting your filters</p>
              )}
            </div>
          ) : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-8" />
                    <TableHead>Timestamp</TableHead>
                    <TableHead>Action</TableHead>
                    <TableHead>Resource</TableHead>
                    <TableHead>Actor</TableHead>
                    <TableHead>IP Address</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.events.map((event) => (
                    <EventDetailRow key={event.id} event={event} />
                  ))}
                </TableBody>
              </Table>
              {data.has_more && (
                <div className="flex justify-center pt-4">
                  <Button variant="outline" onClick={loadMore}>
                    Load More
                  </Button>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
