import { useState, useMemo, useEffect, useRef, forwardRef } from 'react'
import { Download, Shield, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { AudioPlayer } from '@/components/AudioPlayer'
import { apiClient } from '@/api/client'
import { cn } from '@/lib/utils'
import type { UnifiedSegment, Speaker } from '@/api/types'

const SPEAKER_COLORS = ['#60a5fa', '#34d399', '#f472b6', '#fbbf24', '#a78bfa', '#fb923c']

function formatTime(seconds: number): string {
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins}:${secs.toString().padStart(2, '0')}`
}

interface TranscriptSegmentRowProps {
  segment: UnifiedSegment
  speakerColors: Record<string, string>
  showSpeakerColumn: boolean
  showRedacted?: boolean
  isActive?: boolean
  onClick?: () => void
}

const TranscriptSegmentRow = forwardRef<HTMLDivElement, TranscriptSegmentRowProps>(
  ({ segment, speakerColors, showSpeakerColumn, showRedacted, isActive, onClick }, ref) => {
    const speakerColor = segment.speaker ? speakerColors[segment.speaker] : undefined
    const displayText = showRedacted && segment.redacted_text ? segment.redacted_text : segment.text

    return (
      <div
        ref={ref}
        className={cn(
          'flex gap-4 py-3 px-2 border-b border-border last:border-0 transition-colors',
          onClick && 'cursor-pointer hover:bg-muted/50',
          isActive && 'bg-primary/10 border-l-2 border-l-primary'
        )}
        onClick={onClick}
      >
        <div className="w-16 flex-shrink-0 text-xs text-muted-foreground font-mono">
          {formatTime(segment.start)}
        </div>
        {showSpeakerColumn && segment.speaker && (
          <div
            className="w-24 flex-shrink-0 text-xs font-medium"
            style={{ color: speakerColor }}
          >
            {segment.speaker}
          </div>
        )}
        <div className="flex-1 text-sm">{displayText}</div>
      </div>
    )
  }
)
TranscriptSegmentRow.displayName = 'TranscriptSegmentRow'

interface ExportButtonsProps {
  type: 'job' | 'session'
  id: string
}

function ExportButtons({ type, id }: ExportButtonsProps) {
  const formats = ['srt', 'vtt', 'txt', 'json'] as const
  const [downloading, setDownloading] = useState<string | null>(null)

  const handleDownload = async (format: typeof formats[number]) => {
    setDownloading(format)
    try {
      if (type === 'job') {
        await apiClient.downloadJobExport(id, format)
      } else {
        await apiClient.downloadSessionExport(id, format)
      }
    } catch (error) {
      console.error(`Failed to download ${format}:`, error)
    } finally {
      setDownloading(null)
    }
  }

  return (
    <div className="flex gap-2">
      {formats.map((format) => (
        <Button
          key={format}
          variant="outline"
          size="sm"
          onClick={() => handleDownload(format)}
          disabled={downloading !== null}
        >
          {downloading === format ? (
            <Loader2 className="h-3 w-3 mr-1 animate-spin" />
          ) : (
            <Download className="h-3 w-3 mr-1" />
          )}
          {format.toUpperCase()}
        </Button>
      ))}
    </div>
  )
}

export interface PIIConfig {
  enabled: boolean
  entitiesDetected?: number
  redactedText?: string
  onToggle: (showRedacted: boolean) => void
  showRedacted: boolean
}

export interface ExportConfig {
  type: 'job' | 'session'
  id: string
}

export interface TranscriptViewerProps {
  segments: UnifiedSegment[]
  speakers?: Speaker[]
  fullText?: string
  enableExport?: boolean
  exportConfig?: ExportConfig
  piiConfig?: PIIConfig
  maxHeight?: string
  emptyMessage?: string
  audioSrc?: string
}

export function TranscriptViewer({
  segments,
  speakers,
  fullText,
  enableExport = false,
  exportConfig,
  piiConfig,
  maxHeight = '500px',
  emptyMessage = 'No transcript available',
  audioSrc,
}: TranscriptViewerProps) {
  const [currentTime, setCurrentTime] = useState(0)
  const [seekTo, setSeekTo] = useState<number | undefined>(undefined)
  const [autoScroll, setAutoScroll] = useState(false)
  const segmentRefs = useRef<Map<string | number, HTMLDivElement>>(new Map())

  // Generate colors for speakers
  const speakerColors: Record<string, string> = {}
  speakers?.forEach((s, i) => {
    speakerColors[s.id] = SPEAKER_COLORS[i % SPEAKER_COLORS.length]
    speakerColors[s.label] = SPEAKER_COLORS[i % SPEAKER_COLORS.length]
  })

  const hasSpeakers = speakers && speakers.length > 0
  const hasSegments = segments.length > 0

  // Check if segments have per-segment redacted_text
  const hasPerSegmentRedaction = segments.some(s => s.redacted_text)
  // Check if PII toggle should be shown
  const showPiiToggle = piiConfig?.enabled && (hasPerSegmentRedaction || piiConfig?.redactedText)

  // Find active segment based on playback time
  const activeSegmentId = useMemo(() => {
    if (!audioSrc) return null
    const active = segments.find(
      (s) => currentTime >= s.start && currentTime < s.end
    )
    return active?.id ?? null
  }, [currentTime, segments, audioSrc])

  // Auto-scroll to active segment
  useEffect(() => {
    if (autoScroll && activeSegmentId !== null) {
      const el = segmentRefs.current.get(activeSegmentId)
      el?.scrollIntoView({
        behavior: 'smooth',
        block: 'center',
      })
    }
  }, [activeSegmentId, autoScroll])

  const handleSegmentClick = (segment: UnifiedSegment) => {
    if (!audioSrc) return
    // Use a new value each time to ensure the effect triggers even when clicking the same segment
    setSeekTo(segment.start)
  }

  const setSegmentRef = (id: string | number) => (el: HTMLDivElement | null) => {
    if (el) {
      segmentRefs.current.set(id, el)
    } else {
      segmentRefs.current.delete(id)
    }
  }

  return (
    <div className="space-y-0">
      {/* Audio player (sticky) */}
      {audioSrc && (
        <AudioPlayer
          src={audioSrc}
          onTimeUpdate={setCurrentTime}
          onAutoScrollChange={setAutoScroll}
          seekTo={seekTo}
        />
      )}

      {/* Header with PII toggle and export buttons */}
      {(showPiiToggle || enableExport) && (
        <div className="flex items-center justify-between px-2 py-4">
          <div className="flex items-center gap-4">
            {showPiiToggle && (
              <div className="flex items-center gap-2">
                <Shield className="h-4 w-4 text-muted-foreground" />
                <div className="flex rounded-md border border-border overflow-hidden">
                  <button
                    onClick={() => piiConfig.onToggle(false)}
                    className={`px-3 py-1 text-xs font-medium transition-colors ${
                      !piiConfig.showRedacted
                        ? 'bg-primary text-primary-foreground'
                        : 'bg-background text-muted-foreground hover:bg-muted'
                    }`}
                  >
                    Original
                  </button>
                  <button
                    onClick={() => piiConfig.onToggle(true)}
                    className={`px-3 py-1 text-xs font-medium transition-colors ${
                      piiConfig.showRedacted
                        ? 'bg-primary text-primary-foreground'
                        : 'bg-background text-muted-foreground hover:bg-muted'
                    }`}
                  >
                    Redacted
                  </button>
                </div>
                {piiConfig.entitiesDetected && piiConfig.entitiesDetected > 0 && (
                  <Badge variant="secondary">
                    {piiConfig.entitiesDetected} PII
                  </Badge>
                )}
              </div>
            )}
          </div>
          {enableExport && exportConfig && (
            <ExportButtons type={exportConfig.type} id={exportConfig.id} />
          )}
        </div>
      )}

      {/* Transcript content */}
      <div className="overflow-y-auto" style={{ maxHeight }}>
        {hasSegments ? (
          // Segment list view (works for both original and redacted)
          <div>
            {segments.map((segment) => (
              <TranscriptSegmentRow
                key={segment.id}
                ref={setSegmentRef(segment.id)}
                segment={segment}
                speakerColors={speakerColors}
                showSpeakerColumn={!!hasSpeakers}
                showRedacted={piiConfig?.showRedacted}
                isActive={segment.id === activeSegmentId}
                onClick={audioSrc ? () => handleSegmentClick(segment) : undefined}
              />
            ))}
          </div>
        ) : piiConfig?.showRedacted && piiConfig?.redactedText ? (
          // Fallback: plain redacted text if no segments
          <p className="text-sm whitespace-pre-wrap px-2">{piiConfig.redactedText}</p>
        ) : fullText ? (
          // Plain text fallback
          <p className="text-sm whitespace-pre-wrap px-2">{fullText}</p>
        ) : (
          // Empty state
          <p className="text-sm text-muted-foreground py-4 text-center">
            {emptyMessage}
          </p>
        )}
      </div>
    </div>
  )
}
