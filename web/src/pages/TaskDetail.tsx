import { useParams, Link } from 'react-router-dom'
import {
  ArrowLeft,
  Clock,
  AlertCircle,
  CheckCircle,
  XCircle,
  Loader2,
  ChevronDown,
  ChevronRight,
  Copy,
  Check,
} from 'lucide-react'
import { useState } from 'react'
import { useTaskArtifacts } from '@/hooks/useTaskArtifacts'
import { useJobTasks } from '@/hooks/useJobTasks'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import type { TaskStatus } from '@/api/types'

const statusConfig: Record<TaskStatus, { icon: React.ElementType; color: string; bg: string }> = {
  pending: { icon: Clock, color: 'text-zinc-400', bg: 'bg-zinc-500/20' },
  ready: { icon: Clock, color: 'text-yellow-400', bg: 'bg-yellow-500/20' },
  running: { icon: Loader2, color: 'text-blue-400', bg: 'bg-blue-500/20' },
  completed: { icon: CheckCircle, color: 'text-green-400', bg: 'bg-green-500/20' },
  failed: { icon: XCircle, color: 'text-red-400', bg: 'bg-red-500/20' },
  skipped: { icon: Clock, color: 'text-zinc-500', bg: 'bg-zinc-500/10' },
}

function MetricCard({
  label,
  value,
  subtext,
}: {
  label: string
  value: string | number | undefined
  subtext?: string
}) {
  return (
    <div className="flex flex-col gap-1 p-3 rounded-lg bg-muted/50">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="text-sm font-medium">{value ?? '-'}</p>
      {subtext && <p className="text-xs text-muted-foreground">{subtext}</p>}
    </div>
  )
}

function CollapsibleSection({
  title,
  defaultOpen = true,
  children,
  actions,
}: {
  title: string
  defaultOpen?: boolean
  children: React.ReactNode
  actions?: React.ReactNode
}) {
  const [isOpen, setIsOpen] = useState(defaultOpen)

  return (
    <Card>
      <CardHeader
        className="cursor-pointer select-none"
        onClick={() => setIsOpen(!isOpen)}
      >
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            {isOpen ? (
              <ChevronDown className="h-4 w-4" />
            ) : (
              <ChevronRight className="h-4 w-4" />
            )}
            <CardTitle className="text-base font-medium">{title}</CardTitle>
          </div>
          {actions && <div onClick={(e) => e.stopPropagation()}>{actions}</div>}
        </div>
      </CardHeader>
      {isOpen && <CardContent>{children}</CardContent>}
    </Card>
  )
}

function JsonViewer({ data, maxHeight = '400px' }: { data: unknown; maxHeight?: string }) {
  const [copied, setCopied] = useState(false)

  const jsonString = JSON.stringify(data, null, 2)

  const handleCopy = async () => {
    await navigator.clipboard.writeText(jsonString)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="relative">
      <Button
        variant="ghost"
        size="sm"
        className="absolute top-2 right-2 h-8 w-8 p-0"
        onClick={handleCopy}
      >
        {copied ? (
          <Check className="h-4 w-4 text-green-400" />
        ) : (
          <Copy className="h-4 w-4" />
        )}
      </Button>
      <pre
        className="bg-zinc-900 rounded-lg p-4 text-xs font-mono overflow-auto"
        style={{ maxHeight }}
      >
        <code className="text-zinc-300">{jsonString}</code>
      </pre>
    </div>
  )
}

function DependencyBadge({
  taskId,
  jobId,
  tasks,
}: {
  taskId: string
  jobId: string
  tasks: { id: string; stage: string; status: string }[]
}) {
  const task = tasks.find((t) => t.id === taskId)
  if (!task) return null

  const config = statusConfig[task.status as TaskStatus] || statusConfig.pending

  return (
    <Link to={`/jobs/${jobId}/tasks/${taskId}`}>
      <div
        className={cn(
          'inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border border-border',
          'hover:border-primary/50 transition-colors',
          config.bg
        )}
      >
        <div
          className={cn(
            'w-2 h-2 rounded-full',
            task.status === 'completed' && 'bg-green-400',
            task.status === 'running' && 'bg-blue-400',
            task.status === 'failed' && 'bg-red-400',
            task.status === 'pending' && 'bg-zinc-400'
          )}
        />
        <span className={cn('text-xs font-medium uppercase', config.color)}>
          {task.stage}
        </span>
      </div>
    </Link>
  )
}

function TranscribeOutputView({ output }: { output: Record<string, unknown> }) {
  // Data may be at top level or nested in a 'data' field
  const data = (output.data as Record<string, unknown>) ?? output
  const text = data.text as string | undefined
  const segments = data.segments as { start: number; end: number; text: string }[] | undefined
  const language = data.language as string | undefined
  const languageConfidence = data.language_confidence as number | undefined

  const formatTime = (seconds: number) => {
    const mins = Math.floor(seconds / 60)
    const secs = Math.floor(seconds % 60)
    return `${mins}:${secs.toString().padStart(2, '0')}`
  }

  return (
    <div className="space-y-4">
      {/* Summary stats */}
      <div className="flex gap-4 text-sm">
        {language && (
          <div className="flex items-center gap-2">
            <span className="text-muted-foreground">Language:</span>
            <span className="font-medium">
              {language.toUpperCase()}
              {languageConfidence && ` (${Math.round(languageConfidence * 100)}%)`}
            </span>
          </div>
        )}
        {segments && (
          <div className="flex items-center gap-2">
            <span className="text-muted-foreground">Segments:</span>
            <span className="font-medium">{segments.length}</span>
          </div>
        )}
      </div>

      {/* Full text */}
      {text && (
        <div>
          <h4 className="text-sm font-medium mb-2">Full Text</h4>
          <div className="bg-zinc-900 rounded-lg p-4 text-sm max-h-[200px] overflow-auto">
            {text}
          </div>
        </div>
      )}

      {/* Segments preview */}
      {segments && segments.length > 0 && (
        <div>
          <h4 className="text-sm font-medium mb-2">Segments (first 10)</h4>
          <div className="space-y-1 max-h-[300px] overflow-auto">
            {segments.slice(0, 10).map((seg, idx) => (
              <div
                key={idx}
                className="flex gap-4 py-2 px-3 rounded bg-zinc-900/50 text-sm"
              >
                <span className="text-muted-foreground font-mono w-20 flex-shrink-0">
                  {formatTime(seg.start)}
                </span>
                <span>{seg.text}</span>
              </div>
            ))}
            {segments.length > 10 && (
              <p className="text-xs text-muted-foreground py-2">
                ... and {segments.length - 10} more segments
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

function DiarizeOutputView({ output }: { output: Record<string, unknown> }) {
  // Data may be at top level or nested in a 'data' field
  const data = (output.data as Record<string, unknown>) ?? output
  // Speakers can be strings or objects with id/label
  const rawSpeakers = data.speakers as string[] | { id: string; label: string }[] | undefined
  // Segments may be named 'segments' or 'diarization_segments'
  const segments = (data.diarization_segments ?? data.segments) as
    | { speaker: string; start: number; end: number }[]
    | undefined

  // Normalize speakers to display format
  const speakers = rawSpeakers?.map((s) =>
    typeof s === 'string' ? s : s.label
  )

  return (
    <div className="space-y-4">
      {/* Speaker summary */}
      {speakers && speakers.length > 0 && (
        <div>
          <h4 className="text-sm font-medium mb-2">Speakers Detected</h4>
          <div className="flex flex-wrap gap-2">
            {speakers.map((speaker, idx) => (
              <div
                key={idx}
                className="px-3 py-1.5 rounded-lg bg-zinc-900 text-sm"
              >
                {speaker}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Segment count */}
      {segments && (
        <div className="text-sm text-muted-foreground">
          {segments.length} speaker segments detected
        </div>
      )}
    </div>
  )
}

function PrepareOutputView({ output }: { output: Record<string, unknown> }) {
  // Data may be at top level or nested in a 'data' field
  const data = (output.data as Record<string, unknown>) ?? output
  const duration = data.duration as number | undefined
  const channels = data.channels as number | undefined
  const sampleRate = data.sample_rate as number | undefined

  return (
    <div className="grid grid-cols-3 gap-4">
      <MetricCard
        label="Duration"
        value={duration ? `${duration.toFixed(1)}s` : undefined}
      />
      <MetricCard label="Channels" value={channels} />
      <MetricCard
        label="Sample Rate"
        value={sampleRate ? `${sampleRate / 1000}kHz` : undefined}
      />
    </div>
  )
}

function AlignOutputView({ output }: { output: Record<string, unknown> }) {
  // Data may be at top level or nested in a 'data' field
  const data = (output.data as Record<string, unknown>) ?? output
  const language = data.language as string | undefined
  const wordTimestamps = data.word_timestamps as boolean | undefined
  const segments = data.segments as { words?: { word: string }[] }[] | undefined
  const warning = data.warning as { reason?: string } | undefined

  // Count total words across all segments
  const totalWords = segments?.reduce(
    (sum, seg) => sum + (seg.words?.length ?? 0),
    0
  )

  return (
    <div className="space-y-4">
      {/* Metrics */}
      <div className="grid grid-cols-3 gap-4">
        <MetricCard
          label="Language"
          value={language?.toUpperCase()}
        />
        <MetricCard
          label="Word Timestamps"
          value={wordTimestamps ? 'Yes' : 'No'}
        />
        <MetricCard
          label="Words Aligned"
          value={totalWords ?? '-'}
          subtext={segments ? `${segments.length} segments` : undefined}
        />
      </div>

      {/* Warning if alignment failed */}
      {warning && (
        <div className="flex items-start gap-2 p-3 rounded-lg bg-yellow-500/10 border border-yellow-500/30">
          <span className="text-yellow-400 text-sm">
            Alignment fallback: {warning.reason}
          </span>
        </div>
      )}
    </div>
  )
}

function MergeOutputView({ output }: { output: Record<string, unknown> }) {
  // Data may be at top level or nested in a 'data' field
  const data = (output.data as Record<string, unknown>) ?? output
  const metadata = data.metadata as Record<string, unknown> | undefined
  const text = data.text as string | undefined
  const segments = data.segments as { text: string }[] | undefined
  const speakers = data.speakers as { id: string }[] | undefined

  // Extract metadata fields
  const language = metadata?.language as string | undefined
  const languageConfidence = metadata?.language_confidence as number | undefined
  const audioDuration = metadata?.audio_duration as number | undefined
  const wordTimestamps = metadata?.word_timestamps as boolean | undefined
  const speakerDetection = metadata?.speaker_detection as string | undefined
  const pipelineStages = metadata?.pipeline_stages as string[] | undefined
  const pipelineWarnings = metadata?.pipeline_warnings as { stage: string; reason: string }[] | undefined

  const formatDuration = (seconds: number) => {
    const mins = Math.floor(seconds / 60)
    const secs = Math.round(seconds % 60)
    return mins > 0 ? `${mins}m ${secs}s` : `${secs}s`
  }

  return (
    <div className="space-y-4">
      {/* Key metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <MetricCard
          label="Language"
          value={language?.toUpperCase()}
          subtext={languageConfidence ? `${Math.round(languageConfidence * 100)}% confidence` : undefined}
        />
        <MetricCard
          label="Duration"
          value={audioDuration ? formatDuration(audioDuration) : undefined}
        />
        <MetricCard
          label="Segments"
          value={segments?.length}
          subtext={text ? `${text.length.toLocaleString()} chars` : undefined}
        />
        <MetricCard
          label="Speakers"
          value={speakers?.length ?? 0}
          subtext={speakerDetection !== 'none' ? speakerDetection : 'no detection'}
        />
      </div>

      {/* Pipeline summary */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs text-muted-foreground">Pipeline:</span>
        {pipelineStages?.map((stage, idx) => (
          <span key={idx} className="text-xs px-2 py-0.5 rounded bg-zinc-800">
            {stage}
          </span>
        ))}
        {wordTimestamps && (
          <span className="text-xs px-2 py-0.5 rounded bg-green-500/20 text-green-400">
            word timestamps
          </span>
        )}
      </div>

      {/* Pipeline warnings */}
      {pipelineWarnings && pipelineWarnings.length > 0 && (
        <div className="space-y-2">
          {pipelineWarnings.map((warning, idx) => (
            <div
              key={idx}
              className="flex items-start gap-2 p-3 rounded-lg bg-yellow-500/10 border border-yellow-500/30"
            >
              <span className="text-yellow-400 text-sm">
                [{warning.stage}] {warning.reason}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Text preview */}
      {text && (
        <div>
          <h4 className="text-sm font-medium mb-2">Transcript Preview</h4>
          <div className="bg-zinc-900 rounded-lg p-4 text-sm max-h-[150px] overflow-auto">
            {text.slice(0, 500)}
            {text.length > 500 && <span className="text-muted-foreground">...</span>}
          </div>
        </div>
      )}
    </div>
  )
}

function OutputViewer({ stage, output }: { stage: string; output: Record<string, unknown> }) {
  const [showRaw, setShowRaw] = useState(false)

  // Render stage-specific view
  const renderStageView = () => {
    switch (stage) {
      case 'transcribe':
      case 'transcribe_ch0':
      case 'transcribe_ch1':
        return <TranscribeOutputView output={output} />
      case 'diarize':
        return <DiarizeOutputView output={output} />
      case 'prepare':
        return <PrepareOutputView output={output} />
      case 'align':
        return <AlignOutputView output={output} />
      case 'merge':
        return <MergeOutputView output={output} />
      default:
        return null
    }
  }

  const stageView = renderStageView()

  return (
    <div className="space-y-4">
      {stageView}

      <div className="border-t border-border pt-4">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setShowRaw(!showRaw)}
          className="text-xs"
        >
          {showRaw ? 'Hide' : 'Show'} Raw JSON
        </Button>
        {showRaw && <JsonViewer data={output} />}
      </div>
    </div>
  )
}

export function TaskDetail() {
  const { jobId, taskId } = useParams()
  const { data: artifact, isLoading, error } = useTaskArtifacts(jobId, taskId)
  const { data: tasksData } = useJobTasks(jobId)

  if (isLoading) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-64" />
        <div className="grid gap-4 md:grid-cols-4">
          {[...Array(4)].map((_, i) => (
            <Skeleton key={i} className="h-20" />
          ))}
        </div>
        <Skeleton className="h-64" />
      </div>
    )
  }

  if (error || !artifact) {
    return (
      <div className="flex flex-col items-center justify-center py-12">
        <AlertCircle className="h-12 w-12 text-red-400 mb-4" />
        <p className="text-red-400">Error loading task</p>
        <Link to={`/jobs/${jobId}`} className="mt-4">
          <Button variant="outline">Back to Job</Button>
        </Link>
      </div>
    )
  }

  const config = statusConfig[artifact.status as TaskStatus] || statusConfig.pending
  const StatusIcon = config.icon

  const formatDuration = (ms: number) => {
    if (ms < 1000) return `${ms}ms`
    const secs = ms / 1000
    if (secs < 60) return `${secs.toFixed(1)}s`
    const mins = Math.floor(secs / 60)
    const remainingSecs = Math.round(secs % 60)
    return `${mins}m ${remainingSecs}s`
  }

  const formatTime = (isoString: string) => {
    return new Date(isoString).toLocaleTimeString()
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <Link to={`/jobs/${jobId}`}>
          <Button variant="ghost" size="icon">
            <ArrowLeft className="h-4 w-4" />
          </Button>
        </Link>
        <div className="flex-1">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold uppercase">{artifact.stage}</h1>
            <div
              className={cn(
                'flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium',
                config.bg,
                config.color
              )}
            >
              <StatusIcon
                className={cn('h-3.5 w-3.5', artifact.status === 'running' && 'animate-spin')}
              />
              {artifact.status}
            </div>
          </div>
          <p className="text-sm text-muted-foreground mt-1">
            Engine: {artifact.engine_id} | Task: {artifact.task_id}
          </p>
        </div>
      </div>

      {/* Error message */}
      {artifact.error && (
        <Card className="border-red-500/50 bg-red-500/10">
          <CardContent className="py-4">
            <div className="flex items-start gap-3">
              <AlertCircle className="h-5 w-5 text-red-400 mt-0.5" />
              <div>
                <p className="font-medium text-red-400">Task Failed</p>
                <p className="text-sm text-red-400/80 mt-1">{artifact.error}</p>
                {artifact.retries > 0 && (
                  <p className="text-xs text-red-400/60 mt-2">
                    Failed after {artifact.retries} retry attempt{artifact.retries > 1 ? 's' : ''}
                  </p>
                )}
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Metrics */}
      <div className="grid gap-4 md:grid-cols-4">
        <MetricCard
          label="Duration"
          value={artifact.duration_ms ? formatDuration(artifact.duration_ms) : '-'}
        />
        <MetricCard
          label="Started"
          value={artifact.started_at ? formatTime(artifact.started_at) : '-'}
        />
        <MetricCard
          label="Completed"
          value={artifact.completed_at ? formatTime(artifact.completed_at) : '-'}
        />
        <MetricCard
          label="Retries"
          value={`${artifact.retries} / 2`}
          subtext={artifact.required ? 'Required' : 'Optional'}
        />
      </div>

      {/* Dependencies */}
      {artifact.dependencies.length > 0 && tasksData?.tasks && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base font-medium">Dependencies</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-2">
              {artifact.dependencies.map((depId) => (
                <DependencyBadge
                  key={depId}
                  taskId={depId}
                  jobId={jobId!}
                  tasks={tasksData.tasks}
                />
              ))}
              <div className="flex items-center text-muted-foreground">
                <ArrowLeft className="h-4 w-4 rotate-180 mx-2" />
                <span className="text-sm">This task</span>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Input */}
      <CollapsibleSection title="Input" defaultOpen={artifact.status === 'failed'}>
        {artifact.input ? (
          <JsonViewer data={artifact.input} />
        ) : (
          <p className="text-sm text-muted-foreground">No input data available</p>
        )}
      </CollapsibleSection>

      {/* Output */}
      <CollapsibleSection title="Output" defaultOpen={artifact.status === 'completed'}>
        {artifact.output ? (
          <OutputViewer stage={artifact.stage} output={artifact.output} />
        ) : artifact.status === 'pending' ? (
          <p className="text-sm text-muted-foreground">Task has not started yet</p>
        ) : artifact.status === 'running' ? (
          <p className="text-sm text-muted-foreground">Task is still running...</p>
        ) : (
          <p className="text-sm text-muted-foreground">
            No output - task failed before producing results
          </p>
        )}
      </CollapsibleSection>
    </div>
  )
}
