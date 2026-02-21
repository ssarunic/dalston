import { useRef, useState, useEffect, useCallback } from 'react'
import {
  Play,
  Pause,
  RotateCcw,
  Download,
  Loader2,
  AlertCircle,
  RefreshCw,
} from 'lucide-react'
import WaveSurfer from 'wavesurfer.js'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'

/** Persistence key for saving playback position per audio src. */
const POSITION_STORAGE_PREFIX = 'dalston:playback:'

function getStorageKey(src: string): string {
  // Use just the pathname portion so query params (signed URL tokens) don't vary the key
  try {
    const url = new URL(src)
    return POSITION_STORAGE_PREFIX + url.pathname
  } catch {
    return POSITION_STORAGE_PREFIX + src
  }
}

export interface SeekRequest {
  time: number
  id: number // Unique ID to trigger re-seek on repeated clicks to same time
}

export interface AudioPlayerProps {
  src: string
  onTimeUpdate?: (time: number) => void
  onAutoScrollChange?: (enabled: boolean) => void
  onNavigateSegment?: (direction: 'prev' | 'next') => void
  seekTo?: SeekRequest // External seek request (from segment click)
}

function formatTime(seconds: number): string {
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins}:${secs.toString().padStart(2, '0')}`
}

function resolveThemeColor(variable: string, alpha?: number, fallback = '#ffffff'): string {
  if (typeof window === 'undefined') return fallback
  const value = getComputedStyle(document.documentElement).getPropertyValue(variable).trim()
  if (!value) return fallback
  return alpha === undefined ? `hsl(${value})` : `hsl(${value} / ${alpha})`
}

export function AudioPlayer({
  src,
  onTimeUpdate,
  onAutoScrollChange,
  onNavigateSegment,
  seekTo,
}: AudioPlayerProps) {
  const waveformRef = useRef<HTMLDivElement>(null)
  const wavesurferRef = useRef<WaveSurfer | null>(null)
  const isPlayingRef = useRef(false)
  const [isPlaying, setIsPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [playbackRate, setPlaybackRate] = useState(1)
  const [autoScroll, setAutoScroll] = useState(false)
  const [isReady, setIsReady] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [retryKey, setRetryKey] = useState(0)

  // Restore position from sessionStorage
  const restoredRef = useRef(false)

  // Initialize WaveSurfer
  useEffect(() => {
    if (!waveformRef.current) return

    // Clear previous error state on retry
    setLoadError(null)

    const waveColor = resolveThemeColor('--foreground', 0.95, '#ffffff')
    const progressColor = resolveThemeColor('--primary', undefined, '#3b82f6')
    const cursorColor = resolveThemeColor('--ring', undefined, '#60a5fa')

    const ws = WaveSurfer.create({
      container: waveformRef.current,
      height: 48,
      waveColor,
      progressColor,
      cursorColor,
      cursorWidth: 2,
      barWidth: 2,
      barGap: 1,
      barRadius: 2,
      normalize: true,
      interact: true,
      url: src,
    })

    ws.on('ready', () => {
      setDuration(ws.getDuration())
      setIsReady(true)
      setLoadError(null)

      // Restore saved position
      if (!restoredRef.current) {
        restoredRef.current = true
        const savedTime = sessionStorage.getItem(getStorageKey(src))
        if (savedTime) {
          const time = parseFloat(savedTime)
          if (!isNaN(time) && time > 0 && time < ws.getDuration()) {
            ws.seekTo(time / ws.getDuration())
          }
        }
      }
    })

    ws.on('error', (err: Error) => {
      console.error('WaveSurfer error:', err)
      setLoadError(err.message || 'Failed to load audio')
      setIsReady(false)
    })

    ws.on('timeupdate', (time: number) => {
      setCurrentTime(time)
      onTimeUpdate?.(time)
    })

    ws.on('play', () => {
      isPlayingRef.current = true
      setIsPlaying(true)
    })
    ws.on('pause', () => {
      isPlayingRef.current = false
      setIsPlaying(false)
    })
    ws.on('finish', () => {
      isPlayingRef.current = false
      setIsPlaying(false)
    })

    wavesurferRef.current = ws

    return () => {
      ws.destroy()
      wavesurferRef.current = null
      isPlayingRef.current = false
      setIsReady(false)
      restoredRef.current = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [src, retryKey])

  // Sync playback rate
  useEffect(() => {
    if (wavesurferRef.current && isReady) {
      wavesurferRef.current.setPlaybackRate(playbackRate)
    }
  }, [playbackRate, isReady])

  // Persist playback position periodically
  useEffect(() => {
    if (!isReady) return

    const interval = setInterval(() => {
      const time = wavesurferRef.current?.getCurrentTime()
      if (time !== undefined && time > 0) {
        sessionStorage.setItem(getStorageKey(src), String(time))
      }
    }, 2000)

    return () => clearInterval(interval)
  }, [src, isReady])

  // Save position on unmount
  useEffect(() => {
    return () => {
      const time = wavesurferRef.current?.getCurrentTime()
      if (time !== undefined && time > 0) {
        sessionStorage.setItem(getStorageKey(src), String(time))
      }
    }
  }, [src])

  const togglePlay = useCallback(() => {
    wavesurferRef.current?.playPause()
  }, [])

  const seek = useCallback(
    (time: number) => {
      if (!wavesurferRef.current || duration === 0) return
      const clamped = Math.max(0, Math.min(time, duration))
      wavesurferRef.current.seekTo(clamped / duration)
    },
    [duration]
  )

  // Track pending seek request (for when player isn't ready yet)
  const pendingSeekRef = useRef<SeekRequest | null>(null)

  const applySeekRequest = useCallback(
    (request: SeekRequest) => {
      const ws = wavesurferRef.current
      if (!ws || !isReady || duration <= 0) {
        pendingSeekRef.current = request
        return
      }

      ws.seekTo(request.time / duration)
      if (!isPlayingRef.current) {
        void ws.play()
      }
      pendingSeekRef.current = null
    },
    [isReady, duration]
  )

  // Handle external seek requests
  useEffect(() => {
    if (seekTo === undefined) return
    applySeekRequest(seekTo)
  }, [seekTo, applySeekRequest])

  // Apply pending seek when player becomes ready
  useEffect(() => {
    if (!isReady || duration <= 0 || !pendingSeekRef.current) return
    applySeekRequest(pendingSeekRef.current)
  }, [isReady, duration, applySeekRequest])

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

      switch (e.code) {
        case 'Space':
          e.preventDefault()
          togglePlay()
          break
        case 'ArrowLeft':
          e.preventDefault()
          seek(currentTime - 5)
          break
        case 'ArrowRight':
          e.preventDefault()
          seek(currentTime + 5)
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
  }, [currentTime, togglePlay, seek, onNavigateSegment])

  const handleAutoScrollToggle = () => {
    const next = !autoScroll
    setAutoScroll(next)
    onAutoScrollChange?.(next)
  }

  return (
    <div className="sticky top-0 z-10 bg-background border-b">
      {/* Waveform */}
      <div className="relative px-3 pt-3">
        <div
          ref={waveformRef}
          className={cn(
            'w-full rounded cursor-pointer',
            !isReady && !loadError && 'opacity-50',
            loadError && 'opacity-20'
          )}
        />
        {!isReady && !loadError && (
          <div className="absolute inset-0 flex items-center justify-center">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        )}
        {loadError && (
          <div className="absolute inset-0 flex items-center justify-center gap-3 bg-background/80">
            <AlertCircle className="h-4 w-4 text-destructive" />
            <span className="text-sm text-destructive">Audio failed to load</span>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setRetryKey((k) => k + 1)}
            >
              <RefreshCw className="h-3 w-3 mr-1" />
              Retry
            </Button>
          </div>
        )}
      </div>

      {/* Controls row */}
      <div className="px-3 pb-3 pt-2 flex items-center gap-2">
        {/* Play/Pause */}
        <Button
          variant="ghost"
          size="icon"
          onClick={togglePlay}
          disabled={!isReady}
          aria-label={isPlaying ? 'Pause' : 'Play'}
        >
          {isPlaying ? (
            <Pause className="h-4 w-4" />
          ) : (
            <Play className="h-4 w-4" />
          )}
        </Button>

        {/* Time display */}
        <span className="text-sm font-mono text-muted-foreground w-24 text-center shrink-0">
          {formatTime(currentTime)} / {formatTime(duration)}
        </span>

        {/* Spacer */}
        <div className="flex-1" />

        {/* Playback speed */}
        <Select
          value={String(playbackRate)}
          onValueChange={(v) => setPlaybackRate(Number(v))}
        >
          <SelectTrigger className="w-16 h-8 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="0.5">0.5x</SelectItem>
            <SelectItem value="0.75">0.75x</SelectItem>
            <SelectItem value="1">1x</SelectItem>
            <SelectItem value="1.25">1.25x</SelectItem>
            <SelectItem value="1.5">1.5x</SelectItem>
            <SelectItem value="2">2x</SelectItem>
          </SelectContent>
        </Select>

        {/* Auto-scroll toggle */}
        <Button
          variant={autoScroll ? 'secondary' : 'ghost'}
          size="icon"
          onClick={handleAutoScrollToggle}
          title={autoScroll ? 'Auto-scroll on' : 'Auto-scroll off'}
          aria-label={autoScroll ? 'Disable auto-scroll' : 'Enable auto-scroll'}
        >
          <RotateCcw className="h-4 w-4" />
        </Button>

        {/* Download */}
        <a
          href={src}
          download
          title="Download audio"
          className="inline-flex items-center justify-center h-10 w-10 rounded-md text-sm font-medium ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 hover:bg-accent hover:text-accent-foreground"
          aria-label="Download audio"
        >
          <Download className="h-4 w-4" />
        </a>
      </div>
    </div>
  )
}
