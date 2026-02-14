import { useParams, Link } from 'react-router-dom'
import {
  ArrowLeft,
  Clock,
  MessageSquare,
  Mic,
  Download,
  ExternalLink,
  AlertCircle,
  Hash,
  Cpu,
} from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useRealtimeSession, useSessionTranscript } from '@/hooks/useRealtimeSessions'
import { apiClient } from '@/api/client'
import type { RealtimeSessionStatus, SessionUtterance } from '@/api/types'

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
    !!session?.store_transcript && !!session?.transcript_uri
  )

  const handleDownloadAudio = async () => {
    if (!sessionId) return
    try {
      const { url } = await apiClient.getSessionAudioUrl(sessionId)
      window.open(url, '_blank')
    } catch (err) {
      console.error('Failed to get audio URL:', err)
    }
  }

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
        <Link to="/realtime" className="flex items-center gap-2 text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-4 w-4" />
          Back to Realtime
        </Link>
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
          <Link to="/realtime" className="text-muted-foreground hover:text-foreground">
            <ArrowLeft className="h-5 w-5" />
          </Link>
          <div>
            <h1 className="text-2xl font-bold font-mono">{session.id}</h1>
            <p className="text-muted-foreground">Realtime Session</p>
          </div>
        </div>
        <SessionStatusBadge status={session.status} />
      </div>

      {session.error && (
        <div className="p-4 bg-destructive/10 text-destructive rounded-md flex items-center gap-2">
          <AlertCircle className="h-5 w-5" />
          {session.error}
        </div>
      )}

      {/* Stats Cards */}
      <div className="grid gap-4 md:grid-cols-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <Clock className="h-4 w-4" />
              Duration
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">
              {formatDuration(session.audio_duration_seconds)}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <MessageSquare className="h-4 w-4" />
              Segments
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{session.segment_count}</div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <Hash className="h-4 w-4" />
              Words
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{session.word_count}</div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <Cpu className="h-4 w-4" />
              Model
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{session.model ?? '-'}</div>
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
            {session.enhancement_job_id && (
              <div>
                <dt className="text-muted-foreground">Enhancement Job</dt>
                <dd className="font-medium">
                  <Link
                    to={`/jobs/${session.enhancement_job_id}`}
                    className="text-primary hover:underline flex items-center gap-1"
                  >
                    View Job
                    <ExternalLink className="h-3 w-3" />
                  </Link>
                </dd>
              </div>
            )}
          </dl>
        </CardContent>
      </Card>

      {/* Storage Card */}
      <Card>
        <CardHeader>
          <CardTitle>Storage</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Mic className={session.store_audio ? 'h-5 w-5 text-green-500' : 'h-5 w-5 text-muted-foreground'} />
              <span>Audio Recording</span>
            </div>
            {session.store_audio && session.audio_uri ? (
              <Button variant="outline" size="sm" onClick={handleDownloadAudio}>
                <Download className="h-4 w-4 mr-2" />
                Download Audio
              </Button>
            ) : (
              <span className="text-muted-foreground text-sm">
                {session.store_audio ? 'Processing...' : 'Not enabled'}
              </span>
            )}
          </div>

          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <MessageSquare className={session.store_transcript ? 'h-5 w-5 text-blue-500' : 'h-5 w-5 text-muted-foreground'} />
              <span>Transcript</span>
            </div>
            {session.store_transcript && session.transcript_uri ? (
              <span className="text-green-500 text-sm">Stored</span>
            ) : (
              <span className="text-muted-foreground text-sm">
                {session.store_transcript ? 'Processing...' : 'Not enabled'}
              </span>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Transcript Card */}
      {transcript && (
        <Card>
          <CardHeader>
            <CardTitle>Transcript</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="prose prose-sm dark:prose-invert max-w-none">
              <p className="whitespace-pre-wrap">{transcript.text}</p>
            </div>
            {transcript.utterances && transcript.utterances.length > 0 && (
              <div className="mt-4 space-y-2">
                <h4 className="font-medium">Segments</h4>
                <div className="space-y-2 max-h-96 overflow-y-auto">
                  {transcript.utterances.map((utt: SessionUtterance, idx: number) => (
                    <div key={idx} className="p-2 bg-muted rounded text-sm">
                      <span className="text-muted-foreground text-xs">
                        {utt.start.toFixed(1)}s - {utt.end.toFixed(1)}s
                      </span>
                      <p>{utt.text}</p>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
