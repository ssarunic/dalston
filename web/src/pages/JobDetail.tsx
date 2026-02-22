import { useState, useEffect, useCallback } from 'react'
import { useParams } from 'react-router-dom'
import {
  Globe,
  Users,
  FileText,
  AlertCircle,
  Trash2,
  Mic,
  ScrollText,
} from 'lucide-react'
import { useJob } from '@/hooks/useJob'
import { useJobTasks } from '@/hooks/useJobTasks'
import { useResourceAuditTrail } from '@/hooks/useAuditLog'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Badge } from '@/components/ui/badge'
import { StatusBadge } from '@/components/StatusBadge'
import { DAGViewer } from '@/components/DAGViewer'
import { BackButton } from '@/components/BackButton'
import { TranscriptViewer } from '@/components/TranscriptViewer'
import { apiClient } from '@/api/client'
import type { RetentionInfo, AuditEvent } from '@/api/types'

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

const ACTION_STYLES: Record<string, string> = {
  created: 'bg-green-500/10 text-green-500 border-green-500/20',
  completed: 'bg-blue-500/10 text-blue-500 border-blue-500/20',
  accessed: 'bg-slate-500/10 text-slate-400 border-slate-500/20',
  exported: 'bg-purple-500/10 text-purple-500 border-purple-500/20',
  deleted: 'bg-red-500/10 text-red-500 border-red-500/20',
  purged: 'bg-orange-500/10 text-orange-500 border-orange-500/20',
  failed: 'bg-red-500/10 text-red-500 border-red-500/20',
  uploaded: 'bg-cyan-500/10 text-cyan-500 border-cyan-500/20',
  cancelled: 'bg-yellow-500/10 text-yellow-500 border-yellow-500/20',
}

function getActionStyle(action: string): string {
  const actionPart = action.split('.').pop() || action
  return ACTION_STYLES[actionPart] || 'bg-slate-500/10 text-slate-400 border-slate-500/20'
}

function AuditTrailSection({ events, isLoading }: { events?: AuditEvent[]; isLoading: boolean }) {
  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base font-medium flex items-center gap-2">
            <ScrollText className="h-4 w-4" />
            Audit Trail
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-2">
            {[...Array(3)].map((_, i) => (
              <Skeleton key={i} className="h-8 w-full" />
            ))}
          </div>
        </CardContent>
      </Card>
    )
  }

  if (!events || events.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base font-medium flex items-center gap-2">
            <ScrollText className="h-4 w-4" />
            Audit Trail
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground text-center py-4">
            No audit events recorded
          </p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base font-medium flex items-center gap-2">
          <ScrollText className="h-4 w-4" />
          Audit Trail
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-3">
          {events.map((event) => (
            <div
              key={event.id}
              className="flex items-center gap-4 text-sm"
            >
              <div className="w-2 h-2 rounded-full bg-muted-foreground" />
              <Badge variant="outline" className={getActionStyle(event.action)}>
                {event.action}
              </Badge>
              <span className="text-muted-foreground">
                {new Date(event.timestamp).toLocaleString()}
              </span>
              <span className="font-mono text-xs text-muted-foreground">
                {event.actor_id}
              </span>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}

export function JobDetail() {
  const { jobId } = useParams()
  const { data: job, isLoading, error } = useJob(jobId)
  const { data: tasksData } = useJobTasks(jobId)
  const { data: auditData, isLoading: auditLoading } = useResourceAuditTrail('job', jobId)
  const [showRedacted, setShowRedacted] = useState(false)
  const [audioUrlData, setAudioUrlData] = useState<{
    forJobId: string
    originalUrl: string
    redactedUrl?: string
  } | null>(null)
  const currentJobId = job?.id
  const hasRedactedAudio = !!job?.pii?.redacted_audio_available
  const isTerminalStatus = job
    ? ['completed', 'failed', 'cancelled'].includes(job.status)
    : false
  const canAccessAudio = isTerminalStatus && !job?.retention?.purged_at

  const fetchAudioUrls = useCallback(async () => {
    if (!jobId || !currentJobId || !canAccessAudio) return null

    try {
      const { url: originalUrl } = await apiClient.getJobAudioUrl(currentJobId)
      let redactedUrl: string | undefined
      if (hasRedactedAudio) {
        try {
          const { url } = await apiClient.getJobRedactedAudioUrl(currentJobId)
          redactedUrl = url
        } catch {
          // Redacted audio unavailable; keep original playback.
        }
      }
      return { forJobId: currentJobId, originalUrl, redactedUrl }
    } catch (err) {
      console.error('Failed to get audio URL:', err)
      return null
    }
  }, [jobId, currentJobId, canAccessAudio, hasRedactedAudio])

  const refreshAudioUrls = useCallback(async () => {
    const urls = await fetchAudioUrls()
    if (urls) {
      setAudioUrlData(urls)
    }
  }, [fetchAudioUrls])

  const resolveAudioDownloadUrl = useCallback(
    async (variant: 'original' | 'redacted') => {
      const urls = await fetchAudioUrls()
      if (!urls) return null
      setAudioUrlData(urls)
      if (variant === 'redacted') {
        return urls.redactedUrl ?? urls.originalUrl
      }
      return urls.originalUrl
    },
    [fetchAudioUrls]
  )

  // Fetch audio URLs for terminal jobs with non-purged audio
  useEffect(() => {
    if (!canAccessAudio) return
    let cancelled = false
    void (async () => {
      const urls = await fetchAudioUrls()
      if (!cancelled && urls) {
        setAudioUrlData(urls)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [canAccessAudio, fetchAudioUrls])

  // Derive audio URLs - only use if fetched for current job and conditions still met
  const audioUrl =
    audioUrlData &&
    audioUrlData.forJobId === job?.id &&
    canAccessAudio
      ? audioUrlData.originalUrl
      : null

  const redactedAudioUrl =
    audioUrlData &&
    audioUrlData.forJobId === job?.id &&
    canAccessAudio
      ? audioUrlData.redactedUrl
      : undefined

  // Show loading state on initial fetch OR when cached data is from a different job
  // This prevents showing stale data from a previously viewed job
  if (isLoading || job?.id !== jobId) {
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
        <BackButton fallbackPath="/jobs" variant="outline" label="Back to Jobs" className="mt-4" />
      </div>
    )
  }

  if (!job) {
    return (
      <div className="flex flex-col items-center justify-center py-12">
        <p className="text-muted-foreground">Job not found</p>
        <BackButton fallbackPath="/jobs" variant="outline" label="Back to Jobs" className="mt-4" />
      </div>
    )
  }

  const formatDuration = (secs: number) => {
    if (secs < 60) return `${secs.toFixed(1)}s`
    const mins = Math.floor(secs / 60)
    const remainingSecs = secs % 60
    return `${mins}m ${remainingSecs.toFixed(1)}s`
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <BackButton fallbackPath="/jobs" />
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
      <div className="grid gap-4 md:grid-cols-6">
        <MetadataCard
          icon={Mic}
          label="Audio"
          value={job.audio_duration_seconds ? formatDuration(job.audio_duration_seconds) : '-'}
        />
        <MetadataCard
          icon={Globe}
          label="Language"
          value={
            // Don't show detected language if transcript is empty (unreliable detection)
            job.result_word_count
              ? job.result_language_code?.toUpperCase() || 'Auto'
              : job.status === 'completed' ? '-' : 'Auto'
          }
        />
        <MetadataCard
          icon={FileText}
          label="Words"
          value={job.result_word_count?.toLocaleString() ?? '-'}
        />
        <MetadataCard
          icon={FileText}
          label="Segments"
          value={job.result_segment_count ?? job.segments?.length ?? 0}
        />
        <MetadataCard
          icon={Users}
          label="Speakers"
          value={job.result_speaker_count ?? (job.speakers?.length ? job.speakers.length : '-')}
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

      {/* Transcript / Audio */}
      {(job.status === 'completed' || canAccessAudio) && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base font-medium">
              {job.status === 'completed' ? 'Transcript' : 'Audio'}
            </CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <TranscriptViewer
              segments={job.status === 'completed' ? (job.segments ?? []) : []}
              speakers={job.speakers}
              fullText={job.status === 'completed' ? job.text : undefined}
              audioSrc={audioUrl ?? undefined}
              redactedAudioSrc={redactedAudioUrl}
              onRefreshAudioUrls={refreshAudioUrls}
              onResolveAudioDownloadUrl={resolveAudioDownloadUrl}
              enableExport={job.status === 'completed'}
              exportConfig={{ type: 'job', id: job.id }}
              emptyMessage={
                job.status === 'completed'
                  ? 'No transcript available'
                  : 'Transcript not available for this job status'
              }
              piiConfig={job.pii?.enabled ? {
                enabled: true,
                entitiesDetected: job.pii.entities_detected,
                redactedText: job.redacted_text,
                onToggle: setShowRedacted,
                showRedacted,
              } : undefined}
            />
          </CardContent>
        </Card>
      )}

      {/* Audit Trail */}
      <AuditTrailSection events={auditData?.events} isLoading={auditLoading} />
    </div>
  )
}
