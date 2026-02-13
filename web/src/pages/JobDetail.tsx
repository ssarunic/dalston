import { useParams, Link } from 'react-router-dom'
import {
  ArrowLeft,
  Clock,
  Globe,
  Users,
  FileText,
  Download,
  AlertCircle,
  Trash2,
} from 'lucide-react'
import { useJob } from '@/hooks/useJob'
import { useJobTasks } from '@/hooks/useJobTasks'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { StatusBadge } from '@/components/StatusBadge'
import { DAGViewer } from '@/components/DAGViewer'
import { apiClient } from '@/api/client'
import type { Segment, RetentionInfo } from '@/api/types'

function MetadataCard({
  icon: Icon,
  label,
  value,
}: {
  icon: React.ElementType
  label: string
  value: string | number | undefined
}) {
  return (
    <div className="flex items-center gap-3 p-3 rounded-lg bg-muted/50">
      <Icon className="h-4 w-4 text-muted-foreground" />
      <div>
        <p className="text-xs text-muted-foreground">{label}</p>
        <p className="text-sm font-medium">{value ?? '-'}</p>
      </div>
    </div>
  )
}

function RetentionCard({ retention }: { retention?: RetentionInfo }) {
  if (!retention) {
    return (
      <div className="flex items-center gap-3 p-3 rounded-lg bg-muted/50">
        <Trash2 className="h-4 w-4 text-muted-foreground" />
        <div>
          <p className="text-xs text-muted-foreground">Retention</p>
          <p className="text-sm font-medium">-</p>
        </div>
      </div>
    )
  }

  const { mode, policy_name, hours, purge_after, purged_at } = retention

  let statusText = ''

  if (purged_at) {
    statusText = 'Purged'
  } else if (mode === 'keep') {
    statusText = 'Indefinitely'
  } else if (mode === 'none') {
    statusText = 'None'
  } else if (purge_after) {
    const purgeDate = new Date(purge_after)
    const now = new Date()
    const diffMs = purgeDate.getTime() - now.getTime()

    if (diffMs <= 0) {
      statusText = 'Pending'
    } else {
      const diffHours = Math.floor(diffMs / (1000 * 60 * 60))
      const diffDays = Math.floor(diffHours / 24)

      if (diffDays > 0) {
        statusText = `In ${diffDays}d ${diffHours % 24}h`
      } else if (diffHours > 0) {
        statusText = `In ${diffHours}h`
      } else {
        const diffMins = Math.floor(diffMs / (1000 * 60))
        statusText = `In ${diffMins}m`
      }
    }
  } else {
    statusText = hours ? `${hours}h` : 'Pending'
  }

  return (
    <div className="flex items-center gap-3 p-3 rounded-lg bg-muted/50">
      <Trash2 className="h-4 w-4 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        <p className="text-xs text-muted-foreground">Retention</p>
        <p className="text-sm font-medium">{statusText}</p>
        {policy_name && (
          <p className="text-xs text-muted-foreground truncate">
            Policy: {policy_name}
          </p>
        )}
      </div>
    </div>
  )
}

function TranscriptSegment({ segment, speakerColors }: { segment: Segment; speakerColors: Record<string, string> }) {
  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60)
    const secs = Math.floor(seconds % 60)
    return `${mins}:${secs.toString().padStart(2, '0')}`
  }

  const speakerColor = segment.speaker ? speakerColors[segment.speaker] : undefined

  return (
    <div className="flex gap-4 py-3 border-b border-border last:border-0">
      <div className="w-16 flex-shrink-0 text-xs text-muted-foreground font-mono">
        {formatTime(segment.start)}
      </div>
      {segment.speaker && (
        <div
          className="w-24 flex-shrink-0 text-xs font-medium"
          style={{ color: speakerColor }}
        >
          {segment.speaker}
        </div>
      )}
      <div className="flex-1 text-sm">{segment.text}</div>
    </div>
  )
}

function TranscriptViewer({ segments, speakers }: { segments: Segment[]; speakers?: { id: string; label: string }[] }) {
  // Generate colors for speakers
  const speakerColors: Record<string, string> = {}
  const colors = ['#60a5fa', '#34d399', '#f472b6', '#fbbf24', '#a78bfa', '#fb923c']
  speakers?.forEach((s, i) => {
    speakerColors[s.id] = colors[i % colors.length]
    speakerColors[s.label] = colors[i % colors.length]
  })

  if (segments.length === 0) {
    return (
      <p className="text-sm text-muted-foreground py-4 text-center">
        No transcript available
      </p>
    )
  }

  return (
    <div className="max-h-[500px] overflow-y-auto">
      {segments.map((segment) => (
        <TranscriptSegment
          key={segment.id}
          segment={segment}
          speakerColors={speakerColors}
        />
      ))}
    </div>
  )
}

function ExportButtons({ jobId }: { jobId: string }) {
  const formats = ['srt', 'vtt', 'txt', 'json'] as const

  return (
    <div className="flex gap-2">
      {formats.map((format) => (
        <a
          key={format}
          href={apiClient.getExportUrl(jobId, format)}
          download
          target="_blank"
          rel="noopener noreferrer"
        >
          <Button variant="outline" size="sm">
            <Download className="h-3 w-3 mr-1" />
            {format.toUpperCase()}
          </Button>
        </a>
      ))}
    </div>
  )
}

export function JobDetail() {
  const { jobId } = useParams()
  const { data: job, isLoading, error } = useJob(jobId)
  const { data: tasksData } = useJobTasks(jobId)

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-64" />
        <div className="grid gap-4 md:grid-cols-5">
          {[...Array(5)].map((_, i) => (
            <Skeleton key={i} className="h-20" />
          ))}
        </div>
        <Skeleton className="h-64" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center py-12">
        <AlertCircle className="h-12 w-12 text-red-400 mb-4" />
        <p className="text-red-400">Error loading job</p>
        <Link to="/jobs" className="mt-4">
          <Button variant="outline">Back to Jobs</Button>
        </Link>
      </div>
    )
  }

  if (!job) {
    return (
      <div className="flex flex-col items-center justify-center py-12">
        <p className="text-muted-foreground">Job not found</p>
        <Link to="/jobs" className="mt-4">
          <Button variant="outline">Back to Jobs</Button>
        </Link>
      </div>
    )
  }

  // Calculate duration
  const duration =
    job.completed_at && job.started_at
      ? Math.round(
          (new Date(job.completed_at).getTime() -
            new Date(job.started_at).getTime()) /
            1000
        )
      : undefined

  const formatDuration = (secs: number) => {
    if (secs < 60) return `${secs}s`
    const mins = Math.floor(secs / 60)
    const remainingSecs = secs % 60
    return `${mins}m ${remainingSecs}s`
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Link to="/jobs">
            <Button variant="ghost" size="icon">
              <ArrowLeft className="h-4 w-4" />
            </Button>
          </Link>
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-bold font-mono">{job.id}</h1>
              <StatusBadge status={job.status} />
            </div>
            <p className="text-sm text-muted-foreground">
              Created {new Date(job.created_at).toLocaleString()}
            </p>
          </div>
        </div>
        {job.status === 'completed' && <ExportButtons jobId={job.id} />}
      </div>

      {/* Error message */}
      {job.error && (
        <Card className="border-red-500/50 bg-red-500/10">
          <CardContent className="py-4">
            <div className="flex items-start gap-3">
              <AlertCircle className="h-5 w-5 text-red-400 mt-0.5" />
              <div>
                <p className="font-medium text-red-400">Job Failed</p>
                <p className="text-sm text-red-400/80 mt-1">{job.error}</p>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Metadata */}
      <div className="grid gap-4 md:grid-cols-5">
        <MetadataCard
          icon={Clock}
          label="Duration"
          value={duration ? formatDuration(duration) : job.status === 'running' ? 'In progress...' : '-'}
        />
        <MetadataCard
          icon={Globe}
          label="Language"
          value={job.language_code?.toUpperCase() || 'Auto'}
        />
        <MetadataCard
          icon={Users}
          label="Speakers"
          value={job.speakers?.length ?? 0}
        />
        <MetadataCard
          icon={FileText}
          label="Segments"
          value={job.segments?.length ?? 0}
        />
        <RetentionCard retention={job.retention} />
      </div>

      {/* Task Pipeline */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base font-medium">Pipeline</CardTitle>
        </CardHeader>
        <CardContent>
          {tasksData?.tasks && jobId ? (
            <DAGViewer tasks={tasksData.tasks} jobId={jobId} />
          ) : (
            <p className="text-sm text-muted-foreground">
              {job.current_stage ? `Current stage: ${job.current_stage}` : 'Loading pipeline...'}
            </p>
          )}
        </CardContent>
      </Card>

      {/* Transcript */}
      {job.status === 'completed' && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base font-medium">Transcript</CardTitle>
          </CardHeader>
          <CardContent>
            {job.segments && job.segments.length > 0 ? (
              <TranscriptViewer segments={job.segments} speakers={job.speakers} />
            ) : job.text ? (
              <div className="prose prose-invert prose-sm max-w-none">
                <p>{job.text}</p>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground py-4 text-center">
                No transcript available
              </p>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
