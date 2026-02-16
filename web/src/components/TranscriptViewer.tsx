import { Download, Shield } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { apiClient } from '@/api/client'
import type { UnifiedSegment, Speaker } from '@/api/types'

const SPEAKER_COLORS = ['#60a5fa', '#34d399', '#f472b6', '#fbbf24', '#a78bfa', '#fb923c']

function formatTime(seconds: number): string {
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins}:${secs.toString().padStart(2, '0')}`
}

function TranscriptSegmentRow({
  segment,
  speakerColors,
  showSpeakerColumn,
  showRedacted,
}: {
  segment: UnifiedSegment
  speakerColors: Record<string, string>
  showSpeakerColumn: boolean
  showRedacted?: boolean
}) {
  const speakerColor = segment.speaker ? speakerColors[segment.speaker] : undefined
  const displayText = showRedacted && segment.redacted_text ? segment.redacted_text : segment.text

  return (
    <div className="flex gap-4 py-3 border-b border-border last:border-0">
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

interface ExportButtonsProps {
  type: 'job' | 'session'
  id: string
}

function ExportButtons({ type, id }: ExportButtonsProps) {
  const formats = ['srt', 'vtt', 'txt', 'json'] as const

  const getUrl = (format: typeof formats[number]) => {
    return type === 'job'
      ? apiClient.getExportUrl(id, format)
      : apiClient.getSessionExportUrl(id, format)
  }

  return (
    <div className="flex gap-2">
      {formats.map((format) => (
        <a
          key={format}
          href={getUrl(format)}
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
}: TranscriptViewerProps) {
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

  return (
    <div className="space-y-4">
      {/* Header with PII toggle and export buttons */}
      {(showPiiToggle || enableExport) && (
        <div className="flex items-center justify-between">
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
                segment={segment}
                speakerColors={speakerColors}
                showSpeakerColumn={!!hasSpeakers}
                showRedacted={piiConfig?.showRedacted}
              />
            ))}
          </div>
        ) : piiConfig?.showRedacted && piiConfig?.redactedText ? (
          // Fallback: plain redacted text if no segments
          <p className="text-sm whitespace-pre-wrap">{piiConfig.redactedText}</p>
        ) : fullText ? (
          // Plain text fallback
          <p className="text-sm whitespace-pre-wrap">{fullText}</p>
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
