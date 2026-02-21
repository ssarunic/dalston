import { useRef, useState, useEffect, useCallback } from 'react'
import Plyr from 'plyr'
import 'plyr/dist/plyr.css'
import {
  RotateCcw,
  Download,
  Loader2,
  AlertCircle,
  RefreshCw,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

/** Persistence key for saving playback position per audio src. */
const POSITION_STORAGE_PREFIX = 'dalston:playback:'

function getStorageKey(src: string): string {
  try {
    const url = new URL(src)
    return POSITION_STORAGE_PREFIX + url.pathname
  } catch {
    return POSITION_STORAGE_PREFIX + src
  }
}

export interface SeekRequest {
  time: number
  id: number
}

export interface AudioPlayerProps {
  src: string
  redactedSrc?: string
  showRedacted?: boolean
  onTimeUpdate?: (time: number) => void
  onAutoScrollChange?: (enabled: boolean) => void
  onNavigateSegment?: (direction: 'prev' | 'next') => void
  seekTo?: SeekRequest
  className?: string
}

export function AudioPlayer({
  src,
  redactedSrc,
  showRedacted,
  onTimeUpdate,
  onAutoScrollChange,
  onNavigateSegment,
  seekTo,
  className,
}: AudioPlayerProps) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const plyrRef = useRef<Plyr | null>(null)
  const isPlayingRef = useRef(false)
  const [isReady, setIsReady] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [retryKey, setRetryKey] = useState(0)
  const [autoScroll, setAutoScroll] = useState(false)
  const [duration, setDuration] = useState(0)

  const restoredRef = useRef(false)
  const pendingSeekRef = useRef<SeekRequest | null>(null)

  // Determine active source based on redacted toggle
  const activeSrc = showRedacted && redactedSrc ? redactedSrc : src

  // Initialize Plyr
  useEffect(() => {
    if (!audioRef.current) return

    setLoadError(null)
    setIsReady(false)

    const player = new Plyr(audioRef.current, {
      controls: ['play', 'progress', 'current-time', 'duration', 'mute', 'settings'],
      settings: ['speed'],
      speed: { selected: 1, options: [0.5, 0.75, 1, 1.25, 1.5, 2] },
      keyboard: { focused: false, global: false }, // We handle keyboard ourselves
      tooltips: { controls: true, seek: true },
      invertTime: false,
    })

    player.on('ready', () => {
      setIsReady(true)
      setDuration(player.duration || 0)

      // Restore saved position
      if (!restoredRef.current) {
        restoredRef.current = true
        const savedTime = sessionStorage.getItem(getStorageKey(activeSrc))
        if (savedTime) {
          const time = parseFloat(savedTime)
          if (!isNaN(time) && time > 0 && time < player.duration) {
            player.currentTime = time
          }
        }
      }

      // Apply pending seek
      if (pendingSeekRef.current) {
        player.currentTime = pendingSeekRef.current.time
        if (!isPlayingRef.current) {
          player.play()
        }
        pendingSeekRef.current = null
      }
    })

    player.on('error', () => {
      setLoadError('Failed to load audio')
      setIsReady(false)
    })

    player.on('timeupdate', () => {
      const time = player.currentTime
      onTimeUpdate?.(time)
    })

    // Use loadedmetadata on the audio element for duration updates
    const handleDurationChange = () => {
      setDuration(player.duration || 0)
    }
    audioRef.current.addEventListener('durationchange', handleDurationChange)

    player.on('play', () => {
      isPlayingRef.current = true
    })

    player.on('pause', () => {
      isPlayingRef.current = false
    })

    player.on('ended', () => {
      isPlayingRef.current = false
    })

    plyrRef.current = player

    const audioEl = audioRef.current

    return () => {
      // Save position before destroying
      if (player.currentTime > 0) {
        sessionStorage.setItem(getStorageKey(activeSrc), String(player.currentTime))
      }
      audioEl?.removeEventListener('durationchange', handleDurationChange)
      player.destroy()
      plyrRef.current = null
      isPlayingRef.current = false
      setIsReady(false)
      restoredRef.current = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSrc, retryKey])

  // Persist playback position periodically
  useEffect(() => {
    if (!isReady) return

    const interval = setInterval(() => {
      const player = plyrRef.current
      if (player && player.currentTime > 0) {
        sessionStorage.setItem(getStorageKey(activeSrc), String(player.currentTime))
      }
    }, 2000)

    return () => clearInterval(interval)
  }, [activeSrc, isReady])

  // Handle external seek requests
  const applySeekRequest = useCallback(
    (request: SeekRequest) => {
      const player = plyrRef.current
      if (!player || !isReady || duration <= 0) {
        pendingSeekRef.current = request
        return
      }

      player.currentTime = request.time
      if (!isPlayingRef.current) {
        player.play()
      }
      pendingSeekRef.current = null
    },
    [isReady, duration]
  )

  useEffect(() => {
    if (seekTo === undefined) return
    applySeekRequest(seekTo)
  }, [seekTo, applySeekRequest])

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement ||
        e.target instanceof HTMLSelectElement
      ) {
        return
      }

      const player = plyrRef.current
      if (!player) return

      switch (e.code) {
        case 'Space':
          e.preventDefault()
          player.togglePlay()
          break
        case 'ArrowLeft':
          e.preventDefault()
          player.currentTime = Math.max(0, player.currentTime - 5)
          break
        case 'ArrowRight':
          e.preventDefault()
          player.currentTime = Math.min(duration, player.currentTime + 5)
          break
        case 'KeyJ':
          if (!e.ctrlKey && !e.metaKey && !e.altKey) {
            e.preventDefault()
            onNavigateSegment?.('next')
          }
          break
        case 'KeyK':
          if (!e.ctrlKey && !e.metaKey && !e.altKey) {
            e.preventDefault()
            onNavigateSegment?.('prev')
          }
          break
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [duration, onNavigateSegment])

  const handleAutoScrollToggle = () => {
    const next = !autoScroll
    setAutoScroll(next)
    onAutoScrollChange?.(next)
  }

  return (
    <div className={cn('flex items-center gap-2', className)}>
      {/* Plyr audio player */}
      <div className="flex-1 min-w-0">
        <audio ref={audioRef} src={activeSrc} preload="metadata" />
      </div>

      {/* Loading state overlay */}
      {!isReady && !loadError && (
        <div className="flex items-center gap-2 text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          <span className="text-xs">Loading...</span>
        </div>
      )}

      {/* Error state */}
      {loadError && (
        <div className="flex items-center gap-2">
          <AlertCircle className="h-4 w-4 text-destructive" />
          <span className="text-xs text-destructive">Failed</span>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setRetryKey((k) => k + 1)}
            className="h-6 px-2"
          >
            <RefreshCw className="h-3 w-3" />
          </Button>
        </div>
      )}

      {/* Auto-scroll toggle */}
      <Button
        variant={autoScroll ? 'secondary' : 'ghost'}
        size="icon"
        className="h-8 w-8 shrink-0"
        onClick={handleAutoScrollToggle}
        title={autoScroll ? 'Auto-scroll on' : 'Auto-scroll off'}
      >
        <RotateCcw className="h-4 w-4" />
      </Button>

      {/* Download button - downloads currently active audio */}
      <Button
        variant="ghost"
        size="icon"
        className="h-8 w-8 shrink-0"
        asChild
        title="Download audio"
      >
        <a href={activeSrc} download>
          <Download className="h-4 w-4" />
        </a>
      </Button>
    </div>
  )
}
