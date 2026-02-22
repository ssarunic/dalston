import { useState, useMemo, useEffect, useRef, useCallback, forwardRef } from 'react'
import { Download, Shield, Loader2 } from 'lucide-react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { AudioPlayer, type SeekRequest } from '@/components/AudioPlayer'
import { apiClient } from '@/api/client'
import { cn } from '@/lib/utils'
import type { UnifiedSegment, Speaker } from '@/api/types'

const SPEAKER_COLORS = ['#60a5fa', '#34d399', '#f472b6', '#fbbf24', '#a78bfa', '#fb923c']

/** Threshold for enabling virtualization. */
const VIRTUALIZATION_THRESHOLD = 100

/** Estimated height of each segment row in pixels. */
const ESTIMATED_ROW_HEIGHT = 52

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
    <div className="flex flex-wrap justify-end gap-2">
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
  redactedAudioSrc?: string
  onRefreshAudioUrls?: () => Promise<void>
  onResolveAudioDownloadUrl?: (variant: 'original' | 'redacted') => Promise<string | null>
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
  redactedAudioSrc,
  onRefreshAudioUrls,
  onResolveAudioDownloadUrl,
}: TranscriptViewerProps) {
  const [currentTime, setCurrentTime] = useState(0)
  const [seekTo, setSeekTo] = useState<SeekRequest | undefined>(undefined)
  const [autoScroll, setAutoScroll] = useState(false)
  const seekIdRef = useRef(0)
  const scrollContainerRef = useRef<HTMLDivElement>(null)

  // Generate colors for speakers (memoized to avoid rebuilding on every render)
  const speakerColors = useMemo(() => {
    const colors: Record<string, string> = {}
    speakers?.forEach((s, i) => {
      colors[s.id] = SPEAKER_COLORS[i % SPEAKER_COLORS.length]
      colors[s.label] = SPEAKER_COLORS[i % SPEAKER_COLORS.length]
    })
    return colors
  }, [speakers])

  const hasSpeakers = speakers && speakers.length > 0
  const hasSegments = segments.length > 0
  const useVirtual = segments.length >= VIRTUALIZATION_THRESHOLD

  // Check if segments have per-segment redacted_text
  const hasPerSegmentRedaction = segments.some(s => s.redacted_text)
  // Check if PII toggle should be shown
  const showPiiToggle = piiConfig?.enabled && (hasPerSegmentRedaction || piiConfig?.redactedText)

  // Track last known segment index for O(1) lookups during continuous playback
  const lastSegmentIndexRef = useRef(-1)

  // Find active segment index based on playback time
  // Optimized: check nearby segments first, fall back to binary search
  const activeSegmentIndex = useMemo(() => {
    if (!audioSrc || segments.length === 0) return -1

    const isInSegment = (idx: number) => {
      const s = segments[idx]
      return currentTime >= s.start && currentTime < s.end
    }

    // Check last known index first (common case: continuous playback)
    const lastIdx = lastSegmentIndexRef.current
    if (lastIdx >= 0 && lastIdx < segments.length) {
      if (isInSegment(lastIdx)) return lastIdx
      // Check next segment (playback moved forward)
      if (lastIdx + 1 < segments.length && isInSegment(lastIdx + 1)) {
        return lastIdx + 1
      }
      // Check previous segment (playback moved backward slightly)
      if (lastIdx > 0 && isInSegment(lastIdx - 1)) {
        return lastIdx - 1
      }
    }

    // Binary search for larger seeks
    let lo = 0
    let hi = segments.length - 1
    while (lo <= hi) {
      const mid = Math.floor((lo + hi) / 2)
      const s = segments[mid]
      if (currentTime < s.start) {
        hi = mid - 1
      } else if (currentTime >= s.end) {
        lo = mid + 1
      } else {
        return mid
      }
    }
    return -1
  }, [currentTime, segments, audioSrc])

  // Update last known index when it changes
  useEffect(() => {
    lastSegmentIndexRef.current = activeSegmentIndex
  }, [activeSegmentIndex])

  const activeSegmentId = activeSegmentIndex >= 0 ? segments[activeSegmentIndex].id : null

  // Virtualizer for large segment lists
  const virtualizer = useVirtualizer({
    count: useVirtual ? segments.length : 0,
    getScrollElement: () => scrollContainerRef.current,
    estimateSize: () => ESTIMATED_ROW_HEIGHT,
    overscan: 10,
  })

  // Auto-scroll to active segment
  useEffect(() => {
    if (!autoScroll || activeSegmentIndex < 0) return

    if (useVirtual) {
      virtualizer.scrollToIndex(activeSegmentIndex, {
        align: 'center',
        behavior: 'smooth',
      })
    } else {
      const el = scrollContainerRef.current?.querySelector(
        `[data-segment-index="${activeSegmentIndex}"]`
      )
      el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, [activeSegmentIndex, autoScroll, useVirtual, virtualizer])

  const handleSegmentClick = (segment: UnifiedSegment) => {
    if (!audioSrc) return
    seekIdRef.current += 1
    setSeekTo({ time: segment.start, id: seekIdRef.current })
  }

  // Navigate to prev/next segment (for j/k keyboard shortcuts)
  const handleNavigateSegment = useCallback(
    (direction: 'prev' | 'next') => {
      if (segments.length === 0) return

      let targetIndex: number
      if (direction === 'next') {
        targetIndex = activeSegmentIndex >= 0
          ? Math.min(activeSegmentIndex + 1, segments.length - 1)
          : 0
      } else {
        targetIndex = activeSegmentIndex >= 0
          ? Math.max(activeSegmentIndex - 1, 0)
          : 0
      }

      const target = segments[targetIndex]
      seekIdRef.current += 1
      setSeekTo({ time: target.start, id: seekIdRef.current })
    },
    [activeSegmentIndex, segments]
  )

  const renderSegmentRow = (segment: UnifiedSegment, index: number) => (
    <TranscriptSegmentRow
      key={segment.id}
      segment={segment}
      speakerColors={speakerColors}
      showSpeakerColumn={!!hasSpeakers}
      showRedacted={piiConfig?.showRedacted}
      isActive={segment.id === activeSegmentId}
      onClick={audioSrc ? () => handleSegmentClick(segment) : undefined}
      ref={(el) => {
        if (el) {
          el.setAttribute('data-segment-index', String(index))
        }
      }}
    />
  )

  return (
    <div className="space-y-0">
      {/* Header bar with PII toggle, player, and export buttons */}
      {(audioSrc || showPiiToggle || enableExport) && (
        <div className="flex flex-wrap items-start gap-3 px-2 py-3 border-b border-border">
          {/* PII toggle */}
          {showPiiToggle && (
            <div className="order-1 flex items-center gap-2 shrink-0">
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

          {/* Audio player */}
          {audioSrc && (
            <AudioPlayer
              src={audioSrc}
              redactedSrc={redactedAudioSrc}
              showRedacted={piiConfig?.showRedacted}
              onTimeUpdate={setCurrentTime}
              onAutoScrollChange={setAutoScroll}
              onNavigateSegment={handleNavigateSegment}
              onRefreshSourceUrls={onRefreshAudioUrls}
              onResolveDownloadUrl={onResolveAudioDownloadUrl}
              seekTo={seekTo}
              className="order-3 w-full sm:order-2 sm:flex-1 min-w-0"
            />
          )}

          {/* Export buttons */}
          {enableExport && exportConfig && (
            <div className="order-2 ml-auto sm:order-3 sm:ml-0 w-full sm:w-auto">
              <ExportButtons type={exportConfig.type} id={exportConfig.id} />
            </div>
          )}
        </div>
      )}

      {/* Transcript content */}
      <div
        ref={scrollContainerRef}
        className="overflow-y-auto"
        style={{ maxHeight }}
      >
        {hasSegments ? (
          useVirtual ? (
            // Virtualized list for large transcripts
            <div
              style={{
                height: `${virtualizer.getTotalSize()}px`,
                width: '100%',
                position: 'relative',
              }}
            >
              {virtualizer.getVirtualItems().map((virtualRow) => {
                const segment = segments[virtualRow.index]
                return (
                  <div
                    key={virtualRow.key}
                    data-index={virtualRow.index}
                    ref={virtualizer.measureElement}
                    style={{
                      position: 'absolute',
                      top: 0,
                      left: 0,
                      width: '100%',
                      transform: `translateY(${virtualRow.start}px)`,
                    }}
                  >
                    <TranscriptSegmentRow
                      segment={segment}
                      speakerColors={speakerColors}
                      showSpeakerColumn={!!hasSpeakers}
                      showRedacted={piiConfig?.showRedacted}
                      isActive={segment.id === activeSegmentId}
                      onClick={audioSrc ? () => handleSegmentClick(segment) : undefined}
                    />
                  </div>
                )
              })}
            </div>
          ) : (
            // Standard list for small transcripts
            <div>
              {segments.map((segment, index) => renderSegmentRow(segment, index))}
            </div>
          )
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
