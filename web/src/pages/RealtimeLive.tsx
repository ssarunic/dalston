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
import { S } from '@/lib/strings'
import { useLiveSession } from '@/contexts/LiveSessionContext'
import { useEngines } from '@/hooks/useEngines'
import { useRealtimeStatus } from '@/hooks/useRealtimeStatus'
import { useModelRegistry } from '@/hooks/useModelRegistry'
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
  const [vocabularyText, setVocabularyText] = useState('')

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
  const { data: registryData } = useModelRegistry({ stage: 'transcribe' })

  // Get engine_ids from available RT workers
  const rtRuntimes = useMemo(() => {
    if (!enginesData?.realtime_engines) return new Set<string>()
    const engine_ids = new Set<string>()
    for (const worker of enginesData.realtime_engines) {
      if (worker.engine_id) {
        engine_ids.add(worker.engine_id)
      }
    }
    return engine_ids
  }, [enginesData])

  // Get models that are downloaded (ready) and match an RT worker's engine_id
  const availableModels = useMemo(() => {
    if (!registryData?.data || rtRuntimes.size === 0) return []
    return registryData.data
      .filter((m) => m.status === 'ready' && rtRuntimes.has(m.engine_id))
      .map((m) => ({
        id: m.loaded_model_id,  // Use loaded_model_id for the request
        label: m.name || m.id,   // Display name
      }))
      .sort((a, b) => a.label.localeCompare(b.label))
  }, [registryData, rtRuntimes])

  // Also track currently loaded models for display hints
  const loadedModels = useMemo(() => {
    if (!enginesData?.realtime_engines) return new Set<string>()
    const models = new Set<string>()
    for (const worker of enginesData.realtime_engines) {
      for (const m of worker.models) {
        models.add(m)
      }
    }
    return models
  }, [enginesData])

  // Check if selected model supports vocabulary boosting based on worker capabilities
  const vocabularyHint = useMemo(() => {
    if (!enginesData?.realtime_engines) {
      return { supported: null, text: null }
    }

    if (!model) {
      // "Any available" selected - check if any worker supports vocabulary
      const anySupportsVocab = enginesData.realtime_engines.some(
        (w) => w.vocabulary_support?.realtime || w.supports_vocabulary
      )
      if (anySupportsVocab) {
        return { supported: null, text: 'Vocabulary support depends on the engine selected' }
      }
      return { supported: false, text: 'No available engines support vocabulary boosting' }
    }

    // Find the engine_id for the selected model
    const selectedModel = registryData?.data?.find((m) => m.loaded_model_id === model)
    if (!selectedModel) {
      return { supported: null, text: null }
    }

    // Find workers with matching engine_id
    const workersForRuntime = enginesData.realtime_engines.filter(
      (w) => w.engine_id === selectedModel.engine_id
    )

    if (workersForRuntime.length === 0) {
      return { supported: null, text: null }
    }

    // Check if any worker with this engine_id supports vocabulary in realtime
    const vocabWorker = workersForRuntime.find(
      (w) => w.vocabulary_support?.realtime || w.supports_vocabulary
    )
    if (vocabWorker) {
      const method = vocabWorker.vocabulary_support?.method
      const methodLabel = method && method !== 'none'
        ? ` (${method.replace('_', ' ')})`
        : ''
      return { supported: true, text: methodLabel ? `Via ${method?.replace('_', ' ')}` : null }
    }
    return { supported: false, text: 'This model does not support vocabulary boosting' }
  }, [model, enginesData, registryData])

  const isAtCapacity = statusData?.status === 'at_capacity'
  const isUnavailable = statusData?.status === 'unavailable'
  const isIdle = state === 'idle' || state === 'completed' || state === 'error'
  const isRecording = state === 'recording'
  const isConnecting = state === 'connecting'
  const isStopping = state === 'stopping'
  const isCompleted = state === 'completed'

  const handleStart = async () => {
    // Parse vocabulary from comma-separated text
    const vocabulary = vocabularyText
      .split(',')
      .map((term) => term.trim())
      .filter((term) => term.length > 0)

    const config: LiveSessionConfig = {
      language,
      model,
      enableVad: true,
      interimResults: true,
      vocabulary: vocabulary.length > 0 ? vocabulary : undefined,
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
            <h1 className="text-xl sm:text-2xl font-bold">{S.realtimeLive.title}</h1>
            <p className="text-sm sm:text-base text-muted-foreground hidden sm:block">
              {S.realtimeLive.subtitle}
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
          <span className="hidden sm:inline">{S.realtimeLive.settingsButton}</span>
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
            <div className="grid gap-4 grid-cols-1 sm:grid-cols-2">
              <div>
                <label className="text-xs text-muted-foreground mb-1 block">
                  {S.realtimeLive.languageLabel}
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
                  {S.realtimeLive.modelLabel}
                </label>
                <Select value={model} onValueChange={setModel}>
                  <SelectTrigger>
                    <SelectValue placeholder={S.realtimeLive.anyAvailable} />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="">{S.realtimeLive.anyAvailable}</SelectItem>
                    {availableModels.map((m) => (
                      <SelectItem key={m.id} value={m.id}>
                        {loadedModels.has(m.id) ? `${m.label} (loaded)` : m.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                {availableModels.length === 0 && rtRuntimes.size > 0 && (
                  <p className="text-xs text-amber-500 mt-1">
                    {S.realtimeLive.noModelsWarning}
                  </p>
                )}
              </div>
              <div className="sm:col-span-2">
                <label className="text-xs text-muted-foreground mb-1 block">
                  {S.realtimeLive.vocabularyLabel}
                </label>
                <input
                  type="text"
                  value={vocabularyText}
                  onChange={(e) => setVocabularyText(e.target.value)}
                  placeholder={S.realtimeLive.vocabularyPlaceholder}
                  className="w-full px-3 py-2 text-sm border rounded-md bg-background"
                />
                {vocabularyHint.text && (
                  <p className={`text-xs mt-1 ${vocabularyHint.supported === false ? 'text-amber-500' : 'text-muted-foreground'}`}>
                    {vocabularyHint.text}
                  </p>
                )}
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
                {S.realtimeLive.unavailable}{' '}
                <button
                  className="underline text-amber-400 hover:text-amber-300"
                  onClick={() => navigate('/engines')}
                >
                  {S.realtimeLive.checkEngineHealth}
                </button>
              </p>
            ) : (
              <p>
                {S.realtimeLive.atCapacity} (
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
            {S.realtimeLive.startSession}
          </Button>
        )}
        {isConnecting && (
          <Button size="lg" className="h-12 sm:h-14 px-6 sm:px-8 text-base gap-2 w-full sm:w-auto max-w-xs" disabled>
            <div className="h-5 w-5 rounded-full border-2 border-current border-t-transparent animate-spin" />
            {S.realtimeLive.connecting}
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
            {S.realtimeLive.stop}
          </Button>
        )}
        {isStopping && (
          <Button size="lg" className="h-12 sm:h-14 px-6 sm:px-8 text-base gap-2 w-full sm:w-auto max-w-xs" disabled>
            <div className="h-5 w-5 rounded-full border-2 border-current border-t-transparent animate-spin" />
            {S.realtimeLive.finishing}
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
            {S.realtimeLive.recording}
          </div>
        )}
      </div>

      {/* Transcript Area */}
      <Card className="flex-1 min-h-0 flex flex-col">
        <CardHeader className="py-3 shrink-0">
          <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
            <CardTitle className="text-sm font-medium">{S.realtimeLive.transcriptTitle}</CardTitle>
            <div className="flex items-center gap-2 sm:gap-3 text-xs text-muted-foreground">
              <span>{formatDuration(durationSeconds)}</span>
              <span>{wordCount} {S.realtimeLive.words}</span>
              <span className="hidden sm:inline">{segments.length} {S.realtimeLive.segments}</span>
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
                <span className="text-muted-foreground">{S.realtimeLive.sessionCompleted}</span>
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
