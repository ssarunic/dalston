import { useRef, useState, useEffect, useCallback } from 'react'
import Plyr from 'plyr'
import 'plyr/dist/plyr.css'
import {
  Download,
  Loader2,
  AlertCircle,
  RefreshCw,
  SkipBack,
  SkipForward,
  ListMusic,
  MoreVertical,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Tooltip } from '@/components/ui/tooltip'
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from '@/components/ui/dropdown-menu'
import { useMediaQuery } from '@/hooks/useMediaQuery'
import { cn } from '@/lib/utils'

/** Persistence key for saving playback position per audio src. */
const POSITION_STORAGE_PREFIX = 'dalston:playback:'

/** Skip amount in seconds for skip buttons */
const SKIP_SECONDS = 10

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
  src?: string
  redactedSrc?: string
  showRedacted?: boolean
  onTimeUpdate?: (time: number) => void
  onAutoScrollChange?: (enabled: boolean) => void
  onNavigateSegment?: (direction: 'prev' | 'next') => void
  onRefreshSourceUrls?: () => Promise<void>
  onResolveDownloadUrl?: (variant: 'original' | 'redacted') => Promise<string | null>
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
  onRefreshSourceUrls,
  onResolveDownloadUrl,
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
  const [isRefreshing, setIsRefreshing] = useState(false)
  const [isDownloading, setIsDownloading] = useState(false)

  const restoredRef = useRef(false)
  const pendingSeekRef = useRef<SeekRequest | null>(null)

  // Determine active source based on redacted toggle
  const activeSrc = showRedacted && redactedSrc ? redactedSrc : src
  const activeVariant: 'original' | 'redacted' =
    showRedacted && redactedSrc ? 'redacted' : 'original'
  const hasActiveSource = Boolean(activeSrc)

  // Mobile: collapse secondary controls into overflow menu
  const isMobile = useMediaQuery('(max-width: 639px)')

  // Initialize Plyr instance.
  // Keep it alive across source switches to avoid teardown/re-init races.
  useEffect(() => {
    if (!hasActiveSource) {
      setIsReady(false)
      setDuration(0)
      return
    }

    // React can transiently clear refs while the previous Plyr instance is torn down.
    // Retry once on next tick instead of getting stuck without a player.
    if (!audioRef.current) {
      setIsReady(false)
      const retryTimer = window.setTimeout(() => {
        setRetryKey((k) => k + 1)
      }, 0)
      return () => window.clearTimeout(retryTimer)
    }

    setLoadError(null)
    setIsReady(false)

    const audioEl = audioRef.current
    // Always use full controls - CSS hides some on mobile to prevent overflow
    const player = new Plyr(audioEl, {
      controls: ['play', 'progress', 'current-time', 'duration', 'mute', 'settings'],
      settings: ['speed'],
      speed: { selected: 1, options: [0.5, 0.75, 1, 1.25, 1.5, 2] },
      keyboard: { focused: false, global: false }, // We handle keyboard ourselves
      tooltips: { controls: true, seek: true },
      invertTime: false,
    })

    const getCurrentStorageKey = () => {
      const currentSource = audioEl.currentSrc || audioEl.getAttribute('src')
      return currentSource ? getStorageKey(currentSource) : null
    }

    const markReady = () => {
      setIsReady(true)
      setDuration(player.duration || 0)

      // Restore saved position
      if (!restoredRef.current) {
        restoredRef.current = true
        const storageKey = getCurrentStorageKey()
        const savedTime = storageKey ? sessionStorage.getItem(storageKey) : null
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
    }

    player.on('ready', markReady)

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
    audioEl.addEventListener('durationchange', handleDurationChange)
    audioEl.addEventListener('loadedmetadata', markReady)
    audioEl.addEventListener('canplay', markReady)
    audioEl.addEventListener('canplaythrough', markReady)

    // If metadata is already available (e.g. cached source), do not wait for events.
    if (audioEl.readyState >= 1) {
      markReady()
    }

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

    return () => {
      // Save position before destroying
      if (player.currentTime > 0) {
        const storageKey = getCurrentStorageKey()
        if (storageKey) {
          sessionStorage.setItem(storageKey, String(player.currentTime))
        }
      }
      audioEl.removeEventListener('durationchange', handleDurationChange)
      audioEl.removeEventListener('loadedmetadata', markReady)
      audioEl.removeEventListener('canplay', markReady)
      audioEl.removeEventListener('canplaythrough', markReady)
      player.destroy()
      plyrRef.current = null
      isPlayingRef.current = false
      setIsReady(false)
      restoredRef.current = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasActiveSource, retryKey])

  // Switch media source without tearing down Plyr.
  useEffect(() => {
    const audioEl = audioRef.current
    if (!audioEl || !activeSrc) return

    const currentSrc = audioEl.getAttribute('src')
    if (currentSrc === activeSrc) return

    restoredRef.current = false
    setLoadError(null)
    setIsReady(false)

    audioEl.src = activeSrc
    audioEl.load()
  }, [activeSrc])

  // Persist playback position periodically
  useEffect(() => {
    if (!isReady || !activeSrc) return

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

  // Apply queued seek once metadata becomes ready.
  useEffect(() => {
    if (!pendingSeekRef.current || !isReady || duration <= 0) return
    applySeekRequest(pendingSeekRef.current)
  }, [isReady, duration, applySeekRequest])

  // Self-heal when a source exists but Plyr instance is missing.
  useEffect(() => {
    if (!activeSrc || plyrRef.current) return
    const retryTimer = window.setTimeout(() => {
      if (!plyrRef.current) {
        setRetryKey((k) => k + 1)
      }
    }, 150)
    return () => window.clearTimeout(retryTimer)
  }, [activeSrc, retryKey])

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

  const handleSkip = (seconds: number) => {
    const player = plyrRef.current
    if (!player) return
    player.currentTime = Math.max(0, Math.min(duration, player.currentTime + seconds))
  }

  const handleAutoScrollToggle = () => {
    const next = !autoScroll
    setAutoScroll(next)
    onAutoScrollChange?.(next)
  }

  const handleRetry = async () => {
    if (isRefreshing) return
    setLoadError(null)
    setIsRefreshing(true)
    try {
      await onRefreshSourceUrls?.()
    } catch (err) {
      console.error('Failed to refresh audio URL:', err)
    } finally {
      setIsRefreshing(false)
      setRetryKey((k) => k + 1)
    }
  }

  const handleDownload = async () => {
    if (isDownloading) return

    setIsDownloading(true)
    try {
      const resolved = onResolveDownloadUrl
        ? await onResolveDownloadUrl(activeVariant)
        : activeSrc
      if (!resolved) return

      // Fetch as blob for consistent download behavior across browsers
      // This avoids opening new tabs and ensures the file actually downloads
      const response = await fetch(resolved)
      if (!response.ok) throw new Error('Failed to fetch audio')
      const blob = await response.blob()
      const blobUrl = URL.createObjectURL(blob)

      // Extract filename from URL or use default
      let filename = 'audio'
      try {
        const urlPath = new URL(resolved).pathname
        const pathFilename = urlPath.split('/').pop()
        if (pathFilename && pathFilename.includes('.')) {
          filename = pathFilename
        } else {
          // Add extension based on blob type
          const ext = blob.type.split('/')[1] || 'mp3'
          filename = `audio.${ext}`
        }
      } catch {
        filename = 'audio.mp3'
      }

      const a = document.createElement('a')
      a.href = blobUrl
      a.download = filename
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(blobUrl)
    } catch (err) {
      console.error('Failed to download audio:', err)
    } finally {
      setIsDownloading(false)
    }
  }

  // Render secondary controls (desktop: inline, mobile: overflow menu)
  const renderSecondaryControls = () => {
    if (isMobile) {
      return (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="h-10 w-10 shrink-0"
              aria-label="More options"
            >
              <MoreVertical className="h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onSelect={() => handleSkip(-SKIP_SECONDS)} disabled={!isReady}>
              <SkipBack className="h-4 w-4 mr-2" />
              Back {SKIP_SECONDS}s
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => handleSkip(SKIP_SECONDS)} disabled={!isReady}>
              <SkipForward className="h-4 w-4 mr-2" />
              Forward {SKIP_SECONDS}s
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={handleAutoScrollToggle}>
              <ListMusic className="h-4 w-4 mr-2" />
              {autoScroll ? 'Disable auto-scroll' : 'Enable auto-scroll'}
            </DropdownMenuItem>
            <DropdownMenuItem
              onSelect={() => void handleDownload()}
              disabled={isDownloading || (!activeSrc && !onResolveDownloadUrl)}
            >
              <Download className="h-4 w-4 mr-2" />
              Download audio
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      )
    }

    return (
      <>
        {/* Auto-scroll toggle */}
        <Tooltip content={autoScroll ? 'Auto-scroll enabled' : 'Enable auto-scroll'} side="bottom">
          <Button
            variant={autoScroll ? 'secondary' : 'ghost'}
            size="icon"
            className="h-10 w-10 shrink-0"
            onClick={handleAutoScrollToggle}
            aria-label={autoScroll ? 'Disable auto-scroll' : 'Enable auto-scroll'}
            aria-pressed={autoScroll}
          >
            <ListMusic className="h-4 w-4" />
          </Button>
        </Tooltip>

        {/* Download button */}
        <Tooltip content="Download audio" side="bottom">
          <Button
            variant="ghost"
            size="icon"
            className="h-10 w-10 shrink-0"
            onClick={() => void handleDownload()}
            disabled={isDownloading || (!activeSrc && !onResolveDownloadUrl)}
            aria-label="Download audio"
          >
            {isDownloading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Download className="h-4 w-4" />
            )}
          </Button>
        </Tooltip>
      </>
    )
  }

  return (
    <div className={cn('flex items-center gap-2 min-w-0 max-w-full', className)}>
      {/* Skip back button - hidden on mobile (in overflow menu) */}
      {!isMobile && (
        <Tooltip content={`Back ${SKIP_SECONDS}s`} side="bottom">
          <Button
            variant="ghost"
            size="icon"
            className="h-10 w-10 shrink-0"
            onClick={() => handleSkip(-SKIP_SECONDS)}
            disabled={!isReady}
            aria-label={`Skip back ${SKIP_SECONDS} seconds`}
          >
            <SkipBack className="h-4 w-4" />
          </Button>
        </Tooltip>
      )}

      {/* Plyr audio player */}
      <div className="flex-1 min-w-0">
        {activeSrc ? (
          <audio ref={audioRef} src={activeSrc} preload="metadata" />
        ) : (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span>Audio unavailable</span>
            <Button
              variant="ghost"
              size="sm"
              onClick={handleRetry}
              disabled={isRefreshing || !onRefreshSourceUrls}
              className="h-8 px-2"
              aria-label="Retry loading audio"
            >
              {isRefreshing ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <RefreshCw className="h-3 w-3" />
              )}
            </Button>
          </div>
        )}
      </div>

      {/* Skip forward button - hidden on mobile (in overflow menu) */}
      {!isMobile && (
        <Tooltip content={`Forward ${SKIP_SECONDS}s`} side="bottom">
          <Button
            variant="ghost"
            size="icon"
            className="h-10 w-10 shrink-0"
            onClick={() => handleSkip(SKIP_SECONDS)}
            disabled={!isReady}
            aria-label={`Skip forward ${SKIP_SECONDS} seconds`}
          >
            <SkipForward className="h-4 w-4" />
          </Button>
        </Tooltip>
      )}

      {/* Loading state overlay */}
      {activeSrc && !isReady && !loadError && (
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
            onClick={handleRetry}
            disabled={isRefreshing}
            className="h-8 px-2"
            aria-label="Retry loading audio"
          >
            {isRefreshing ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <RefreshCw className="h-3 w-3" />
            )}
          </Button>
        </div>
      )}

      {/* Secondary controls (desktop: inline buttons, mobile: overflow menu) */}
      {renderSecondaryControls()}
    </div>
  )
}
