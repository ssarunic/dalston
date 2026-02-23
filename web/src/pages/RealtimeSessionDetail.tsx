import { useState, useEffect, useCallback } from 'react'
import { useParams, Link } from 'react-router-dom'
import {
  Clock,
  MessageSquare,
  ExternalLink,
  AlertCircle,
  Hash,
  Cpu,
  Archive,
} from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { StatusBadge } from '@/components/StatusBadge'
import { useRealtimeSession, useSessionTranscript } from '@/hooks/useRealtimeSessions'
import { BackButton } from '@/components/BackButton'
import { TranscriptViewer } from '@/components/TranscriptViewer'
import { apiClient } from '@/api/client'
import { formatRetentionDisplay, formatPurgeCountdown } from '@/lib/retention'

function formatDuration(seconds: number): string {
  if (seconds < 60) {
    return `${Math.round(seconds)}s`
  }
  const mins = Math.floor(seconds / 60)
  const secs = Math.round(seconds % 60)
  if (mins < 60) {
    return `${mins}m ${secs}s`
  }
  const hrs = Math.floor(mins / 60)
  const remainMins = mins % 60
  return `${hrs}h ${remainMins}m ${secs}s`
}

function formatDate(dateStr: string | null | undefined): string {
  if (!dateStr) return '-'
  const date = new Date(dateStr)
  return date.toLocaleString()
}

export function RealtimeSessionDetail() {
  const { sessionId } = useParams<{ sessionId: string }>()
  const { data: session, isLoading, error } = useRealtimeSession(sessionId)
  const { data: transcript } = useSessionTranscript(
    sessionId,
    session?.retention !== 0 && !!session?.transcript_uri
  )
  const [audioUrlData, setAudioUrlData] = useState<{ forSessionId: string; url: string } | null>(null)
  const canAccessAudio = session?.retention !== 0 && !!session?.audio_uri

  const fetchAudioUrl = useCallback(async () => {
    if (!sessionId || !canAccessAudio) return null
    try {
      const { url } = await apiClient.getSessionAudioUrl(sessionId)
      return { forSessionId: sessionId, url }
    } catch (err) {
      console.error('Failed to get audio URL:', err)
      return null
    }
  }, [sessionId, canAccessAudio])

  const refreshAudioUrls = useCallback(async () => {
    const data = await fetchAudioUrl()
    if (data) {
      setAudioUrlData(data)
    }
  }, [fetchAudioUrl])

  const resolveAudioDownloadUrl = useCallback(async (variant: 'original' | 'redacted') => {
    void variant
    const data = await fetchAudioUrl()
    if (data) {
      setAudioUrlData(data)
    }
    return data?.url ?? null
  }, [fetchAudioUrl])

  // Fetch audio URL for sessions with stored audio
  useEffect(() => {
    if (!canAccessAudio) return
    let cancelled = false
    void (async () => {
      const data = await fetchAudioUrl()
      if (!cancelled && data) {
        setAudioUrlData(data)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [canAccessAudio, fetchAudioUrl])

  // Derive audio URL - only use if fetched for current session and conditions still met
  const audioUrl =
    audioUrlData &&
    audioUrlData.forSessionId === sessionId &&
    canAccessAudio
      ? audioUrlData.url
      : null

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-64 w-full" />
      </div>
    )
  }

  if (error || !session) {
    return (
      <div className="space-y-6">
        <BackButton fallbackPath="/realtime" variant="link" label="Back to Realtime" />
        <div className="p-4 bg-destructive/10 text-destructive rounded-md flex items-center gap-2">
          <AlertCircle className="h-5 w-5" />
          Session not found
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <BackButton fallbackPath="/realtime" />
          <div>
            <h1 className="text-2xl font-bold font-mono">{session.id}</h1>
            <p className="text-muted-foreground">Realtime Session</p>
          </div>
        </div>
        <StatusBadge status={session.status} />
      </div>

      {session.error && (
        <div className="p-4 bg-destructive/10 text-destructive rounded-md flex items-center gap-2">
          <AlertCircle className="h-5 w-5" />
          {session.error}
        </div>
      )}

      {/* Stats Cards */}
      <div className="grid gap-4 md:grid-cols-5">
        <Card>
          <CardHeader className="pb-2">
            <span className="text-sm font-medium text-muted-foreground flex items-center gap-2">
              <Clock className="h-4 w-4" />
              Duration
            </span>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {formatDuration(session.audio_duration_seconds)}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <span className="text-sm font-medium text-muted-foreground flex items-center gap-2">
              <MessageSquare className="h-4 w-4" />
              Segments
            </span>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{session.segment_count}</div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <span className="text-sm font-medium text-muted-foreground flex items-center gap-2">
              <Hash className="h-4 w-4" />
              Words
            </span>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{session.word_count}</div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <span className="text-sm font-medium text-muted-foreground flex items-center gap-2">
              <Cpu className="h-4 w-4" />
              Model
            </span>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{session.model ?? '-'}</div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <span className="text-sm font-medium text-muted-foreground flex items-center gap-2">
              <Archive className="h-4 w-4" />
              Retention
            </span>
          </CardHeader>
          <CardContent>
            {session.purged_at ? (
              <>
                <div className="text-2xl font-bold">{formatRetentionDisplay(session.retention)}</div>
                <p className="text-sm text-muted-foreground">Purged</p>
              </>
            ) : session.retention === 0 ? (
              <>
                <div className="text-2xl font-bold">Transient</div>
                <p className="text-sm text-muted-foreground">No storage</p>
              </>
            ) : session.retention === -1 ? (
              <div className="text-2xl font-bold">Permanent</div>
            ) : (
              <>
                <div className="text-2xl font-bold">{formatRetentionDisplay(session.retention)}</div>
                <p className="text-sm text-muted-foreground">
                  {session.purge_after
                    ? `${formatPurgeCountdown(session.purge_after).text} ${formatPurgeCountdown(session.purge_after).subtitle ?? ''}`
                    : 'after session end'}
                </p>
              </>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Details Card */}
      <Card>
        <CardHeader>
          <CardTitle>Session Details</CardTitle>
        </CardHeader>
        <CardContent>
          <dl className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <dt className="text-muted-foreground">Language</dt>
              <dd className="font-medium">{session.language ?? 'auto'}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Encoding</dt>
              <dd className="font-medium">{session.encoding ?? '-'}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Sample Rate</dt>
              <dd className="font-medium">{session.sample_rate ? `${session.sample_rate} Hz` : '-'}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Worker</dt>
              <dd className="font-medium font-mono text-xs">{session.worker_id ?? '-'}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Client IP</dt>
              <dd className="font-medium font-mono">{session.client_ip ?? '-'}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Started At</dt>
              <dd className="font-medium">{formatDate(session.started_at)}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Ended At</dt>
              <dd className="font-medium">{formatDate(session.ended_at)}</dd>
            </div>
            {session.previous_session_id && (
              <div>
                <dt className="text-muted-foreground">Previous Session</dt>
                <dd className="font-medium">
                  <Link
                    to={`/realtime/sessions/${session.previous_session_id}`}
                    className="text-primary hover:underline flex items-center gap-1"
                  >
                    {session.previous_session_id.slice(0, 12)}...
                    <ExternalLink className="h-3 w-3" />
                  </Link>
                </dd>
              </div>
            )}
          </dl>
        </CardContent>
      </Card>

      {/* Transcript/Audio Card */}
      {(transcript || canAccessAudio) && (
        <Card>
          <CardHeader>
            <CardTitle>{transcript ? 'Transcript' : 'Audio'}</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <TranscriptViewer
              segments={transcript?.utterances?.map((utt) => ({
                id: utt.id,
                start: utt.start,
                end: utt.end,
                text: utt.text,
              })) ?? []}
              fullText={transcript?.text}
              audioSrc={audioUrl ?? undefined}
              showAudioPlayer={canAccessAudio}
              onRefreshAudioUrls={refreshAudioUrls}
              onResolveAudioDownloadUrl={resolveAudioDownloadUrl}
              enableExport={!!transcript && !!session.transcript_uri}
              exportConfig={{ type: 'session', id: session.id }}
              emptyMessage={
                transcript
                  ? 'No transcript available'
                  : 'Transcript not stored for this session'
              }
              showSectionTitle={false}
            />
          </CardContent>
        </Card>
      )}

    </div>
  )
}
