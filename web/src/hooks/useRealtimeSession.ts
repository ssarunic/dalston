import { useCallback, useEffect, useRef, useState } from 'react'
import { useAuth } from '@/contexts/AuthContext'
import type { LiveTranscriptSegment, LiveSessionConfig, LiveSessionState } from '@/api/types'

interface UseRealtimeSessionReturn {
  state: LiveSessionState
  sessionId: string | null
  segments: LiveTranscriptSegment[]
  partialText: string
  isSpeaking: boolean
  audioLevel: number
  durationSeconds: number
  wordCount: number
  error: string | null
  start: (config: LiveSessionConfig) => Promise<void>
  stop: () => void
}

// Count words in a string
function countWords(text: string): number {
  const trimmed = text.trim()
  if (trimmed.length === 0) return 0
  return trimmed.split(/\s+/).length
}

export function useRealtimeSession(): UseRealtimeSessionReturn {
  const { apiKey } = useAuth()
  const [state, setState] = useState<LiveSessionState>('idle')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [segments, setSegments] = useState<LiveTranscriptSegment[]>([])
  const [partialText, setPartialText] = useState('')
  const [isSpeaking, setIsSpeaking] = useState(false)
  const [audioLevel, setAudioLevel] = useState(0)
  const [durationSeconds, setDurationSeconds] = useState(0)
  const [error, setError] = useState<string | null>(null)

  // Refs for cleanup
  const wsRef = useRef<WebSocket | null>(null)
  const audioContextRef = useRef<AudioContext | null>(null)
  const mediaStreamRef = useRef<MediaStream | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const segmentCounterRef = useRef(0)

  const wordCount = segments.reduce((sum, seg) => sum + countWords(seg.text), 0)

  // Cleanup all resources
  const cleanup = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
    if (audioContextRef.current) {
      audioContextRef.current.close().catch(() => {})
      audioContextRef.current = null
    }
    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach((t) => t.stop())
      mediaStreamRef.current = null
    }
    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
    }
    setAudioLevel(0)
    setIsSpeaking(false)
  }, [])

  // Cleanup on unmount
  useEffect(() => cleanup, [cleanup])

  const stop = useCallback(() => {
    if (state !== 'recording') return
    setState('stopping')

    // Send end message and let the server respond with session.end
    const ws = wsRef.current
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: 'end' }))
    }

    // Stop audio capture immediately
    if (audioContextRef.current) {
      audioContextRef.current.close().catch(() => {})
      audioContextRef.current = null
    }
    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach((t) => t.stop())
      mediaStreamRef.current = null
    }
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }

    // Timeout: if server doesn't respond within 5s, close anyway
    setTimeout(() => {
      if (wsRef.current) {
        wsRef.current.close()
        wsRef.current = null
      }
      setState((prev) => (prev === 'stopping' ? 'completed' : prev))
    }, 5000)
  }, [state])

  const start = useCallback(
    async (config: LiveSessionConfig) => {
      if (state !== 'idle' && state !== 'completed' && state !== 'error') return
      if (!apiKey) {
        setError('No API key available')
        return
      }

      // Reset state
      setSegments([])
      setPartialText('')
      setError(null)
      setDurationSeconds(0)
      setSessionId(null)
      segmentCounterRef.current = 0
      setState('connecting')

      try {
        // Request microphone access
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            sampleRate: 16000,
            channelCount: 1,
            echoCancellation: true,
            noiseSuppression: true,
          },
        })
        mediaStreamRef.current = stream

        // Build WebSocket URL
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
        const wsUrl = new URL(
          `${protocol}//${window.location.host}/v1/audio/transcriptions/stream`
        )
        wsUrl.searchParams.set('api_key', apiKey)
        wsUrl.searchParams.set('language', config.language || 'auto')
        if (config.model) {
          wsUrl.searchParams.set('model', config.model)
        }
        wsUrl.searchParams.set('encoding', 'pcm_s16le')
        wsUrl.searchParams.set('sample_rate', '16000')
        wsUrl.searchParams.set('enable_vad', String(config.enableVad))
        wsUrl.searchParams.set('interim_results', String(config.interimResults))
        wsUrl.searchParams.set('retention', '30')

        // Open WebSocket
        const ws = new WebSocket(wsUrl.toString())
        ws.binaryType = 'arraybuffer'
        wsRef.current = ws

        // Set up audio pipeline once WS is open
        ws.onopen = async () => {
          try {
            const audioContext = new AudioContext({ sampleRate: 16000 })
            audioContextRef.current = audioContext

            // Load the AudioWorklet processor
            const processorUrl = import.meta.env.BASE_URL + 'pcm-processor.js'
            await audioContext.audioWorklet.addModule(processorUrl)

            const source = audioContext.createMediaStreamSource(stream)
            const workletNode = new AudioWorkletNode(audioContext, 'pcm-processor')

            workletNode.port.onmessage = (event) => {
              const { pcm, audioLevel: level } = event.data
              setAudioLevel(level)

              // Send PCM data to WebSocket
              if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
                wsRef.current.send(pcm)
              }
            }

            source.connect(workletNode)
            workletNode.connect(audioContext.destination)
          } catch (audioErr) {
            const msg =
              audioErr instanceof Error ? audioErr.message : 'Failed to initialize audio'
            setError(msg)
            setState('error')
            cleanup()
          }
        }

        ws.onmessage = (event) => {
          if (typeof event.data !== 'string') return

          let msg: Record<string, unknown>
          try {
            msg = JSON.parse(event.data)
          } catch {
            return
          }

          switch (msg.type) {
            case 'session.begin':
              setSessionId(msg.session_id as string)
              setState('recording')
              // Start duration timer
              timerRef.current = setInterval(() => {
                setDurationSeconds((prev) => prev + 1)
              }, 1000)
              break

            case 'transcript.partial':
              setPartialText((msg.text as string) || '')
              break

            case 'transcript.final': {
              const segId = `seg_${String(segmentCounterRef.current++).padStart(4, '0')}`
              const newSegment: LiveTranscriptSegment = {
                id: segId,
                text: (msg.text as string) || '',
                start: (msg.start as number) || 0,
                end: (msg.end as number) || 0,
                confidence: msg.confidence as number | undefined,
              }
              if (msg.words) {
                newSegment.words = msg.words as LiveTranscriptSegment['words']
              }
              setSegments((prev) => [...prev, newSegment])
              setPartialText('')
              break
            }

            case 'vad.speech_start':
              setIsSpeaking(true)
              break

            case 'vad.speech_end':
              setIsSpeaking(false)
              break

            case 'session.end':
              setState('completed')
              setPartialText('')
              setIsSpeaking(false)
              setAudioLevel(0)
              if (timerRef.current) {
                clearInterval(timerRef.current)
                timerRef.current = null
              }
              if (wsRef.current) {
                wsRef.current.close()
                wsRef.current = null
              }
              break

            case 'error': {
              const errMsg = (msg.message as string) || 'Transcription error'
              const recoverable = msg.recoverable as boolean
              if (!recoverable) {
                setError(errMsg)
                setState('error')
                cleanup()
              }
              break
            }
          }
        }

        ws.onerror = () => {
          setError('WebSocket connection error')
          setState('error')
          cleanup()
        }

        ws.onclose = (event) => {
          // Only set error if we weren't expecting the close
          setState((prev) => {
            if (prev === 'stopping' || prev === 'completed') return 'completed'
            if (prev === 'error') return 'error'
            // Unexpected close
            if (event.code !== 1000) {
              setError(
                event.reason || `Connection closed unexpectedly (code ${event.code})`
              )
              cleanup()
              return 'error'
            }
            return 'completed'
          })
        }
      } catch (err) {
        let msg = 'Failed to start session'
        if (err instanceof DOMException && err.name === 'NotAllowedError') {
          msg = 'Microphone access denied. Please allow microphone access and try again.'
        } else if (err instanceof DOMException && err.name === 'NotFoundError') {
          msg = 'No microphone found. Please connect a microphone and try again.'
        } else if (err instanceof Error) {
          msg = err.message
        }
        setError(msg)
        setState('error')
        cleanup()
      }
    },
    [apiKey, state, cleanup]
  )

  return {
    state,
    sessionId,
    segments,
    partialText,
    isSpeaking,
    audioLevel,
    durationSeconds,
    wordCount,
    error,
    start,
    stop,
  }
}
