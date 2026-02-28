import { useEffect, useRef } from 'react'
import type { LiveTranscriptSegment } from '@/api/types'

function formatTimestamp(seconds: number): string {
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`
}

interface LiveTranscriptProps {
  segments: LiveTranscriptSegment[]
  partialText: string
  isActive: boolean
}

export function LiveTranscript({ segments, partialText, isActive }: LiveTranscriptProps) {
  const scrollRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to bottom on new content
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [segments, partialText])

  const hasContent = segments.length > 0 || partialText.length > 0

  if (!hasContent) {
    return (
      <div className="flex-1 flex items-center justify-center text-muted-foreground text-sm">
        {isActive
          ? 'Listening... Start speaking to see your transcript.'
          : 'Start a session to see your transcript here.'}
      </div>
    )
  }

  return (
    <div ref={scrollRef} className="flex-1 overflow-y-auto space-y-3 pr-1">
      {segments.map((segment) => (
        <div key={segment.id} className="flex gap-3">
          <span className="text-xs text-muted-foreground font-mono pt-0.5 shrink-0 w-12 text-right">
            {formatTimestamp(segment.start)}
          </span>
          <p className="text-sm leading-relaxed">{segment.text}</p>
        </div>
      ))}
      {partialText && (
        <div className="flex gap-3">
          <span className="text-xs text-muted-foreground font-mono pt-0.5 shrink-0 w-12 text-right">
            &nbsp;
          </span>
          <p className="text-sm leading-relaxed text-muted-foreground italic">
            {partialText}
          </p>
        </div>
      )}
    </div>
  )
}
