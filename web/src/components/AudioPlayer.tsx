import { useRef, useState, useEffect, useCallback } from 'react'
import { Play, Pause, RotateCcw, Download } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { cn } from '@/lib/utils'

interface AudioPlayerProps {
  src: string
  onTimeUpdate?: (time: number) => void
  onAutoScrollChange?: (enabled: boolean) => void
  seekTo?: number // External seek request (from segment click)
}

function formatTime(seconds: number): string {
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins}:${secs.toString().padStart(2, '0')}`
}

export function AudioPlayer({
  src,
  onTimeUpdate,
  onAutoScrollChange,
  seekTo,
}: AudioPlayerProps) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const [isPlaying, setIsPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [playbackRate, setPlaybackRate] = useState(1)
  const [autoScroll, setAutoScroll] = useState(false)

  const togglePlay = useCallback(() => {
    if (!audioRef.current) return
    if (isPlaying) {
      audioRef.current.pause()
    } else {
      audioRef.current.play()
    }
    setIsPlaying(!isPlaying)
  }, [isPlaying])

  const seek = useCallback(
    (time: number) => {
      if (!audioRef.current) return
      audioRef.current.currentTime = Math.max(0, Math.min(time, duration))
    },
    [duration]
  )

  // Handle external seek requests
  useEffect(() => {
    if (seekTo !== undefined && audioRef.current) {
      audioRef.current.currentTime = seekTo
      if (!isPlaying) {
        audioRef.current.play()
        setIsPlaying(true)
      }
    }
  }, [seekTo]) // eslint-disable-line react-hooks/exhaustive-deps

  // Sync playback rate
  useEffect(() => {
    if (audioRef.current) {
      audioRef.current.playbackRate = playbackRate
    }
  }, [playbackRate])

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Don't capture when typing in input fields or textareas
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
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [currentTime, togglePlay, seek])

  const handleAutoScrollToggle = () => {
    const next = !autoScroll
    setAutoScroll(next)
    onAutoScrollChange?.(next)
  }

  const handleTimeUpdate = () => {
    const time = audioRef.current?.currentTime ?? 0
    setCurrentTime(time)
    onTimeUpdate?.(time)
  }

  const handleLoadedMetadata = () => {
    setDuration(audioRef.current?.duration ?? 0)
  }

  const handleEnded = () => {
    setIsPlaying(false)
  }

  const handleSeekChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    seek(Number(e.target.value))
  }

  return (
    <div className="sticky top-0 z-10 bg-background border-b p-3 flex items-center gap-3">
      {/* Play/Pause */}
      <Button
        variant="ghost"
        size="icon"
        onClick={togglePlay}
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

      {/* Seek slider (native range input) */}
      <input
        type="range"
        min={0}
        max={duration || 100}
        step={0.1}
        value={currentTime}
        onChange={handleSeekChange}
        className={cn(
          'flex-1 h-2 rounded-lg appearance-none cursor-pointer',
          'bg-muted accent-primary'
        )}
        aria-label="Seek audio"
      />

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

      {/* Hidden audio element */}
      <audio
        ref={audioRef}
        src={src}
        onTimeUpdate={handleTimeUpdate}
        onLoadedMetadata={handleLoadedMetadata}
        onEnded={handleEnded}
        preload="metadata"
      />
    </div>
  )
}
