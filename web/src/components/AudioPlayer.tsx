import { useRef, useState, useEffect, useCallback } from 'react'
import {
  Play,
  Pause,
  RotateCcw,
  Download,
  Repeat,
  Scissors,
  Loader2,
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

export interface AudioPlayerProps {
  src: string
  onTimeUpdate?: (time: number) => void
  onAutoScrollChange?: (enabled: boolean) => void
  onNavigateSegment?: (direction: 'prev' | 'next') => void
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
  onNavigateSegment,
  seekTo,
}: AudioPlayerProps) {
  const waveformRef = useRef<HTMLDivElement>(null)
  const wavesurferRef = useRef<WaveSurfer | null>(null)
  const [isPlaying, setIsPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [playbackRate, setPlaybackRate] = useState(1)
  const [autoScroll, setAutoScroll] = useState(false)
  const [isReady, setIsReady] = useState(false)

  // A-B loop state
  const [loopA, setLoopA] = useState<number | null>(null)
  const [loopB, setLoopB] = useState<number | null>(null)
  const isLooping = loopA !== null && loopB !== null

  // Clip export state
  const [isExporting, setIsExporting] = useState(false)

  // Restore position from sessionStorage
  const restoredRef = useRef(false)

  // Initialize WaveSurfer
  useEffect(() => {
    if (!waveformRef.current) return

    const ws = WaveSurfer.create({
      container: waveformRef.current,
      height: 48,
      waveColor: 'hsl(var(--muted-foreground) / 0.35)',
      progressColor: 'hsl(var(--primary))',
      cursorColor: 'hsl(var(--primary))',
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

    ws.on('timeupdate', (time: number) => {
      setCurrentTime(time)
      onTimeUpdate?.(time)
    })

    ws.on('play', () => setIsPlaying(true))
    ws.on('pause', () => setIsPlaying(false))
    ws.on('finish', () => setIsPlaying(false))

    wavesurferRef.current = ws

    return () => {
      ws.destroy()
      wavesurferRef.current = null
      setIsReady(false)
      restoredRef.current = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [src])

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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [src])

  // A-B loop enforcement
  useEffect(() => {
    if (!isLooping || !wavesurferRef.current || !isReady) return

    const ws = wavesurferRef.current
    const checkLoop = (time: number) => {
      if (loopB !== null && time >= loopB) {
        ws.seekTo(loopA! / duration)
      }
    }

    ws.on('timeupdate', checkLoop)
    return () => {
      ws.un('timeupdate', checkLoop)
    }
  }, [loopA, loopB, isLooping, duration, isReady])

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

  // Handle external seek requests
  useEffect(() => {
    if (seekTo !== undefined && wavesurferRef.current && isReady && duration > 0) {
      wavesurferRef.current.seekTo(seekTo / duration)
      if (!isPlaying) {
        wavesurferRef.current.play()
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seekTo])

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

  // A-B loop toggle: first press sets A, second sets B, third clears
  const handleLoopToggle = () => {
    if (loopA === null) {
      setLoopA(currentTime)
    } else if (loopB === null) {
      // B must be after A
      if (currentTime > loopA) {
        setLoopB(currentTime)
      } else {
        // If cursor is before A, reset and set new A
        setLoopA(currentTime)
        setLoopB(null)
      }
    } else {
      // Clear loop
      setLoopA(null)
      setLoopB(null)
    }
  }

  // Clip export: download the audio between A and B markers
  const handleClipExport = async () => {
    if (!isLooping || isExporting) return
    setIsExporting(true)

    try {
      const response = await fetch(src)
      const arrayBuffer = await response.arrayBuffer()

      const audioCtx = new AudioContext()
      const audioBuffer = await audioCtx.decodeAudioData(arrayBuffer)

      const sampleRate = audioBuffer.sampleRate
      const startSample = Math.floor(loopA! * sampleRate)
      const endSample = Math.floor(loopB! * sampleRate)
      const clipLength = endSample - startSample

      if (clipLength <= 0) {
        audioCtx.close()
        return
      }

      const channels = audioBuffer.numberOfChannels
      const clipBuffer = audioCtx.createBuffer(channels, clipLength, sampleRate)

      for (let ch = 0; ch < channels; ch++) {
        const source = audioBuffer.getChannelData(ch)
        const dest = clipBuffer.getChannelData(ch)
        for (let i = 0; i < clipLength; i++) {
          dest[i] = source[startSample + i]
        }
      }

      // Encode as WAV
      const wavBlob = encodeWav(clipBuffer)
      const url = URL.createObjectURL(wavBlob)
      const a = document.createElement('a')
      a.href = url
      a.download = `clip_${formatTime(loopA!).replace(':', 'm')}s-${formatTime(loopB!).replace(':', 'm')}s.wav`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
      audioCtx.close()
    } catch (err) {
      console.error('Failed to export clip:', err)
    } finally {
      setIsExporting(false)
    }
  }

  return (
    <div className="sticky top-0 z-10 bg-background border-b">
      {/* Waveform */}
      <div className="relative px-3 pt-3">
        <div
          ref={waveformRef}
          className={cn(
            'w-full rounded cursor-pointer',
            !isReady && 'opacity-50'
          )}
        />
        {/* A-B loop region overlay */}
        {loopA !== null && duration > 0 && (
          <div
            className="absolute top-3 bottom-0 bg-primary/10 border-l-2 border-primary pointer-events-none"
            style={{
              left: `calc(0.75rem + ${(loopA / duration) * 100}%)`,
              width: loopB !== null
                ? `${((loopB - loopA) / duration) * 100}%`
                : '2px',
            }}
          >
            {loopB !== null && (
              <div className="absolute right-0 top-0 bottom-0 border-r-2 border-primary" />
            )}
          </div>
        )}
        {!isReady && (
          <div className="absolute inset-0 flex items-center justify-center">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
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

        {/* A-B Loop toggle */}
        <Button
          variant={isLooping ? 'secondary' : loopA !== null ? 'outline' : 'ghost'}
          size="icon"
          onClick={handleLoopToggle}
          title={
            isLooping
              ? `Loop ${formatTime(loopA!)} - ${formatTime(loopB!)} (click to clear)`
              : loopA !== null
                ? `A: ${formatTime(loopA)} (click at B point)`
                : 'Set loop A point'
          }
          aria-label="Toggle A-B loop"
          className="relative"
        >
          <Repeat className="h-4 w-4" />
          {loopA !== null && !isLooping && (
            <span className="absolute -top-1 -right-1 w-3 h-3 rounded-full bg-primary text-[8px] text-primary-foreground flex items-center justify-center font-bold">
              A
            </span>
          )}
        </Button>

        {/* Clip export (only when loop is active) */}
        {isLooping && (
          <Button
            variant="ghost"
            size="icon"
            onClick={handleClipExport}
            disabled={isExporting}
            title={`Export clip ${formatTime(loopA!)} - ${formatTime(loopB!)}`}
            aria-label="Export clip"
          >
            {isExporting ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Scissors className="h-4 w-4" />
            )}
          </Button>
        )}

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

      {/* Loop info bar */}
      {isLooping && (
        <div className="px-3 pb-2 flex items-center gap-2 text-xs text-muted-foreground">
          <Repeat className="h-3 w-3 text-primary" />
          <span>
            Loop: {formatTime(loopA!)} - {formatTime(loopB!)}
          </span>
          <button
            onClick={() => { setLoopA(null); setLoopB(null) }}
            className="text-primary hover:underline ml-1"
          >
            Clear
          </button>
        </div>
      )}
    </div>
  )
}

/** Encode an AudioBuffer as a WAV Blob. */
function encodeWav(buffer: AudioBuffer): Blob {
  const numChannels = buffer.numberOfChannels
  const sampleRate = buffer.sampleRate
  const length = buffer.length
  const bytesPerSample = 2
  const blockAlign = numChannels * bytesPerSample
  const dataSize = length * blockAlign
  const headerSize = 44
  const totalSize = headerSize + dataSize

  const arrayBuffer = new ArrayBuffer(totalSize)
  const view = new DataView(arrayBuffer)

  // RIFF header
  writeString(view, 0, 'RIFF')
  view.setUint32(4, totalSize - 8, true)
  writeString(view, 8, 'WAVE')

  // fmt chunk
  writeString(view, 12, 'fmt ')
  view.setUint32(16, 16, true) // chunk size
  view.setUint16(20, 1, true) // PCM format
  view.setUint16(22, numChannels, true)
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, sampleRate * blockAlign, true)
  view.setUint16(32, blockAlign, true)
  view.setUint16(34, bytesPerSample * 8, true)

  // data chunk
  writeString(view, 36, 'data')
  view.setUint32(40, dataSize, true)

  // Interleave channels
  let offset = 44
  const channels: Float32Array[] = []
  for (let ch = 0; ch < numChannels; ch++) {
    channels.push(buffer.getChannelData(ch))
  }

  for (let i = 0; i < length; i++) {
    for (let ch = 0; ch < numChannels; ch++) {
      const sample = Math.max(-1, Math.min(1, channels[ch][i]))
      view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7FFF, true)
      offset += 2
    }
  }

  return new Blob([arrayBuffer], { type: 'audio/wav' })
}

function writeString(view: DataView, offset: number, str: string) {
  for (let i = 0; i < str.length; i++) {
    view.setUint8(offset + i, str.charCodeAt(i))
  }
}
