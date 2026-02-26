import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Mic,
  Square,
  Settings2,
  ChevronDown,
  ChevronUp,
  ExternalLink,
  AlertCircle,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { AudioLevelMeter } from '@/components/AudioLevelMeter'
import { BackButton } from '@/components/BackButton'
import { LiveTranscript } from '@/components/LiveTranscript'
import { useLiveSession } from '@/contexts/LiveSessionContext'
import { useEngines } from '@/hooks/useEngines'
import { useRealtimeStatus } from '@/hooks/useRealtimeStatus'
import type { LiveSessionConfig } from '@/api/types'

const LANGUAGES = [
  { value: 'auto', label: 'Auto-detect' },
  { value: 'en', label: 'English' },
  { value: 'es', label: 'Spanish' },
  { value: 'fr', label: 'French' },
  { value: 'de', label: 'German' },
  { value: 'it', label: 'Italian' },
  { value: 'pt', label: 'Portuguese' },
  { value: 'nl', label: 'Dutch' },
  { value: 'ja', label: 'Japanese' },
  { value: 'ko', label: 'Korean' },
  { value: 'zh', label: 'Chinese' },
  { value: 'ru', label: 'Russian' },
  { value: 'ar', label: 'Arabic' },
  { value: 'hi', label: 'Hindi' },
  { value: 'pl', label: 'Polish' },
  { value: 'uk', label: 'Ukrainian' },
  { value: 'sv', label: 'Swedish' },
  { value: 'da', label: 'Danish' },
  { value: 'fi', label: 'Finnish' },
  { value: 'no', label: 'Norwegian' },
  { value: 'tr', label: 'Turkish' },
]

function formatDuration(seconds: number): string {
  const mins = Math.floor(seconds / 60)
  const secs = seconds % 60
  return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`
}

export function RealtimeLive() {
  const navigate = useNavigate()
  const [showSettings, setShowSettings] = useState(false)
  const [language, setLanguage] = useState('auto')
  const [model, setModel] = useState('')
  const [enableVad, setEnableVad] = useState(true)
  const [interimResults, setInterimResults] = useState(true)

  const {
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
  } = useLiveSession()

  const { data: enginesData } = useEngines()
  const { data: statusData } = useRealtimeStatus()

  // Extract available models from realtime engines
  const availableModels = useMemo(() => {
    if (!enginesData?.realtime_engines) return []
    const models = new Set<string>()
    for (const worker of enginesData.realtime_engines) {
      for (const m of worker.models) {
        models.add(m)
      }
    }
    return Array.from(models).sort()
  }, [enginesData])

  const isAtCapacity = statusData?.status === 'at_capacity'
  const isUnavailable = statusData?.status === 'unavailable'
  const isIdle = state === 'idle' || state === 'completed' || state === 'error'
  const isRecording = state === 'recording'
  const isConnecting = state === 'connecting'
  const isStopping = state === 'stopping'
  const isCompleted = state === 'completed'

  const handleStart = async () => {
    const config: LiveSessionConfig = {
      language,
      model,
      enableVad,
      interimResults,
    }
    await start(config)
  }

  return (
    <div className="flex flex-col h-[calc(100vh-4rem)] gap-4">
      {/* Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between shrink-0">
        <div className="flex items-center gap-3 sm:gap-4">
          <BackButton fallbackPath="/realtime" />
          <div>
            <h1 className="text-xl sm:text-2xl font-bold">Live Transcription</h1>
            <p className="text-sm sm:text-base text-muted-foreground hidden sm:block">
              Start a real-time transcription session using your microphone
            </p>
          </div>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => setShowSettings(!showSettings)}
          disabled={isRecording || isConnecting || isStopping}
          className="self-start sm:self-auto"
        >
          <Settings2 className="h-4 w-4 sm:mr-2" />
          <span className="hidden sm:inline">Settings</span>
          {showSettings ? (
            <ChevronUp className="h-4 w-4 sm:ml-1" />
          ) : (
            <ChevronDown className="h-4 w-4 sm:ml-1" />
          )}
        </Button>
      </div>

      {/* Settings Panel (collapsible) */}
      {showSettings && (
        <Card className="shrink-0">
          <CardContent className="pt-4 pb-4">
            <div className="grid gap-4 grid-cols-2 sm:grid-cols-4">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">
                  Language
                </label>
                <Select value={language} onValueChange={setLanguage}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {LANGUAGES.map((lang) => (
                      <SelectItem key={lang.value} value={lang.value}>
                        {lang.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">
                  Model
                </label>
                <Select value={model} onValueChange={setModel}>
                  <SelectTrigger>
                    <SelectValue placeholder="Any available" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="">Any available</SelectItem>
                    {availableModels.map((m) => (
                      <SelectItem key={m} value={m}>
                        {m}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="flex items-end">
                <label className="flex items-center gap-2 text-sm cursor-pointer">
                  <input
                    type="checkbox"
                    checked={enableVad}
                    onChange={(e) => setEnableVad(e.target.checked)}
                    className="rounded border-border"
                  />
                  VAD events
                </label>
              </div>
              <div className="flex items-end">
                <label className="flex items-center gap-2 text-sm cursor-pointer">
                  <input
                    type="checkbox"
                    checked={interimResults}
                    onChange={(e) => setInterimResults(e.target.checked)}
                    className="rounded border-border"
                  />
                  Interim results
                </label>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Status Warning */}
      {isIdle && (isAtCapacity || isUnavailable) && (
        <div className="shrink-0 p-3 rounded-md border border-amber-500/40 bg-amber-500/5 flex items-start gap-2">
          <AlertCircle className="h-4 w-4 text-amber-500 mt-0.5 shrink-0" />
          <div className="text-sm">
            {isUnavailable ? (
              <p>
                Real-time transcription is currently unavailable. No workers are
                ready.{' '}
                <button
                  className="underline text-amber-400 hover:text-amber-300"
                  onClick={() => navigate('/engines')}
                >
                  Check engine health
                </button>
              </p>
            ) : (
              <p>
                All worker capacity is currently in use (
                {statusData?.active_sessions}/{statusData?.total_capacity}).
                You may need to wait for a session to finish.
              </p>
            )}
          </div>
        </div>
      )}

      {/* Error display */}
      {error && (
        <div className="shrink-0 p-3 rounded-md bg-destructive/10 text-destructive flex items-start gap-2">
          <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
          <div className="text-sm flex-1">
            <p>{error}</p>
          </div>
        </div>
      )}

      {/* Central Action Area */}
      <div className="shrink-0 flex flex-col items-center gap-3 py-2 sm:py-4">
        {/* Main Button */}
        {isIdle && (
          <Button
            size="lg"
            className="h-12 sm:h-14 px-6 sm:px-8 text-base gap-2 w-full sm:w-auto max-w-xs"
            onClick={handleStart}
            disabled={isUnavailable}
          >
            <Mic className="h-5 w-5" />
            Start Session
          </Button>
        )}
        {isConnecting && (
          <Button size="lg" className="h-12 sm:h-14 px-6 sm:px-8 text-base gap-2 w-full sm:w-auto max-w-xs" disabled>
            <div className="h-5 w-5 rounded-full border-2 border-current border-t-transparent animate-spin" />
            Connecting...
          </Button>
        )}
        {isRecording && (
          <Button
            size="lg"
            variant="destructive"
            className="h-12 sm:h-14 px-6 sm:px-8 text-base gap-2 w-full sm:w-auto max-w-xs"
            onClick={stop}
          >
            <Square className="h-5 w-5" />
            Stop
          </Button>
        )}
        {isStopping && (
          <Button size="lg" className="h-12 sm:h-14 px-6 sm:px-8 text-base gap-2 w-full sm:w-auto max-w-xs" disabled>
            <div className="h-5 w-5 rounded-full border-2 border-current border-t-transparent animate-spin" />
            Finishing...
          </Button>
        )}

        {/* Audio Level + VAD Indicator */}
        {(isRecording || isConnecting) && (
          <div className="flex items-center gap-3 w-full max-w-xs">
            <span
              className={`inline-block w-2 h-2 rounded-full shrink-0 transition-colors ${
                isSpeaking ? 'bg-green-500' : 'bg-muted-foreground/30'
              }`}
            />
            <AudioLevelMeter
              level={audioLevel}
              isSpeaking={isSpeaking}
              isActive={isRecording}
            />
          </div>
        )}

        {/* Recording pulse */}
        {isRecording && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className="inline-block w-2 h-2 rounded-full bg-red-500 animate-pulse" />
            Recording
          </div>
        )}
      </div>

      {/* Transcript Area */}
      <Card className="flex-1 min-h-0 flex flex-col">
        <CardHeader className="py-3 shrink-0">
          <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
            <CardTitle className="text-sm font-medium">Transcript</CardTitle>
            <div className="flex items-center gap-2 sm:gap-3 text-xs text-muted-foreground">
              <span>{formatDuration(durationSeconds)}</span>
              <span>{wordCount} words</span>
              <span className="hidden sm:inline">{segments.length} segments</span>
            </div>
          </div>
        </CardHeader>
        <CardContent className="flex-1 min-h-0 flex flex-col pb-4">
          <LiveTranscript
            segments={segments}
            partialText={partialText}
            isActive={isRecording}
          />
        </CardContent>
      </Card>

      {/* Post-Session Actions */}
      {isCompleted && sessionId && (
        <Card className="shrink-0">
          <CardContent className="py-3 sm:py-4">
            <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between sm:gap-3">
              <div className="text-sm">
                <span className="text-muted-foreground">Session completed</span>
                <span className="font-mono text-xs ml-2 text-muted-foreground hidden sm:inline">
                  {sessionId.slice(0, 16)}...
                </span>
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => navigate(`/realtime/sessions/${sessionId}`)}
                className="w-full sm:w-auto"
              >
                <ExternalLink className="h-4 w-4 mr-1" />
                View Details
              </Button>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
