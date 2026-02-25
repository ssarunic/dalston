import { useState, useRef, useCallback, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { Upload, Link, AlertCircle, ChevronDown, ChevronUp, X, Info } from 'lucide-react'
import { BackButton } from '@/components/BackButton'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { useCreateJob } from '@/hooks/useCreateJob'
import { useMediaQuery } from '@/hooks/useMediaQuery'
import { useCapabilities, useEnginesList } from '@/hooks/useCapabilities'
import type {
  SpeakerDetection,
  TimestampsGranularity,
  PIITier,
  PIIRedactionMode,
} from '@/api/types'

type SourceType = 'file' | 'url'

// Language code to display name mapping
const LANGUAGE_NAMES: Record<string, string> = {
  en: 'English',
  es: 'Spanish',
  fr: 'French',
  de: 'German',
  it: 'Italian',
  pt: 'Portuguese',
  nl: 'Dutch',
  ja: 'Japanese',
  ko: 'Korean',
  zh: 'Chinese',
  ar: 'Arabic',
  ru: 'Russian',
  hi: 'Hindi',
  pl: 'Polish',
  tr: 'Turkish',
  vi: 'Vietnamese',
  th: 'Thai',
  cs: 'Czech',
  ro: 'Romanian',
  hu: 'Hungarian',
  el: 'Greek',
  da: 'Danish',
  fi: 'Finnish',
  no: 'Norwegian',
  sv: 'Swedish',
  he: 'Hebrew',
  id: 'Indonesian',
  ms: 'Malay',
  uk: 'Ukrainian',
  bg: 'Bulgarian',
  ca: 'Catalan',
  hr: 'Croatian',
  sk: 'Slovak',
  sl: 'Slovenian',
  sr: 'Serbian',
  lt: 'Lithuanian',
  lv: 'Latvian',
  et: 'Estonian',
  ta: 'Tamil',
  te: 'Telugu',
  bn: 'Bengali',
  mr: 'Marathi',
  gu: 'Gujarati',
  kn: 'Kannada',
  ml: 'Malayalam',
  pa: 'Punjabi',
  ur: 'Urdu',
  fa: 'Persian',
  sw: 'Swahili',
  tl: 'Tagalog',
  af: 'Afrikaans',
  cy: 'Welsh',
  gl: 'Galician',
  eu: 'Basque',
  is: 'Icelandic',
  mt: 'Maltese',
  ga: 'Irish',
  sq: 'Albanian',
  mk: 'Macedonian',
  bs: 'Bosnian',
  az: 'Azerbaijani',
  kk: 'Kazakh',
  uz: 'Uzbek',
  mn: 'Mongolian',
  ne: 'Nepali',
  si: 'Sinhala',
  km: 'Khmer',
  lo: 'Lao',
  my: 'Burmese',
  ka: 'Georgian',
  am: 'Amharic',
  yo: 'Yoruba',
  zu: 'Zulu',
  jv: 'Javanese',
  su: 'Sundanese',
}

function getLanguageLabel(code: string): string {
  return LANGUAGE_NAMES[code] || code.toUpperCase()
}

const SPEAKER_DETECTION_OPTIONS: { value: SpeakerDetection; label: string }[] = [
  { value: 'none', label: 'None' },
  { value: 'diarize', label: 'Diarize' },
  { value: 'per_channel', label: 'Per channel' },
]

const TIMESTAMPS_OPTIONS: { value: TimestampsGranularity; label: string }[] = [
  { value: 'none', label: 'None' },
  { value: 'segment', label: 'Segment' },
  { value: 'word', label: 'Word' },
]

const PII_TIER_OPTIONS: { value: PIITier; label: string }[] = [
  { value: 'fast', label: 'Fast' },
  { value: 'standard', label: 'Standard' },
  { value: 'thorough', label: 'Thorough' },
]

const PII_REDACTION_MODE_OPTIONS: { value: PIIRedactionMode; label: string }[] = [
  { value: 'silence', label: 'Silence' },
  { value: 'beep', label: 'Beep' },
]

type RetentionMode = 'default' | 'transient' | 'permanent' | 'days'

const RETENTION_OPTIONS: { value: RetentionMode; label: string }[] = [
  { value: 'default', label: 'Server default' },
  { value: 'transient', label: "Don't store" },
  { value: 'permanent', label: 'Keep forever' },
  { value: 'days', label: 'Delete after...' },
]

export function NewJob() {
  const navigate = useNavigate()
  const isMobile = useMediaQuery('(max-width: 767px)')
  const createJob = useCreateJob()
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Fetch capabilities and engines for dynamic options
  const { data: capabilities } = useCapabilities()
  const { data: enginesList } = useEnginesList()

  // Source
  const [sourceType, setSourceType] = useState<SourceType>('file')
  const [file, setFile] = useState<File | null>(null)
  const [audioUrl, setAudioUrl] = useState('')
  const [isDragOver, setIsDragOver] = useState(false)

  // Basic settings
  const [language, setLanguage] = useState('auto')
  const [speakerDetection, setSpeakerDetection] = useState<SpeakerDetection>('none')
  const [numSpeakers, setNumSpeakers] = useState<string>('')
  const [timestampsGranularity, setTimestampsGranularity] = useState<TimestampsGranularity>('segment')

  // Advanced settings
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [model, setModel] = useState('auto')
  const [minSpeakers, setMinSpeakers] = useState<string>('')
  const [maxSpeakers, setMaxSpeakers] = useState<string>('')
  const [vocabulary, setVocabulary] = useState('')
  const [retentionMode, setRetentionMode] = useState<RetentionMode>('default')
  const [retentionDays, setRetentionDays] = useState('30')

  // Compute available models (running transcribe engines)
  const availableModels = useMemo(() => {
    if (!enginesList?.engines) return []
    return enginesList.engines
      .filter((e) => e.stage === 'transcribe' && e.status === 'running')
      .map((e) => ({ id: e.id, name: e.name || e.id, languages: e.capabilities.languages }))
  }, [enginesList])

  // Compute available languages based on capabilities and selected model
  const languageOptions = useMemo(() => {
    let languages: string[] = []

    if (model !== 'auto' && availableModels.length > 0) {
      // Filter to languages supported by selected model
      const selectedEngine = availableModels.find((m) => m.id === model)
      if (selectedEngine?.languages && selectedEngine.languages.length > 0) {
        languages = selectedEngine.languages
      } else {
        // Engine supports all languages (null means all)
        languages = capabilities?.languages || []
      }
    } else {
      // Use aggregate capabilities
      languages = capabilities?.languages || []
    }

    // If "*" in list, it means all languages supported - show common ones
    if (languages.includes('*') || languages.length === 0) {
      languages = ['en', 'es', 'fr', 'de', 'it', 'pt', 'nl', 'ja', 'ko', 'zh', 'ar', 'ru', 'hi']
    }

    // Sort and map to options
    return [
      { value: 'auto', label: 'Auto-detect' },
      ...languages
        .filter((l) => l !== '*')
        .sort((a, b) => getLanguageLabel(a).localeCompare(getLanguageLabel(b)))
        .map((code) => ({ value: code, label: getLanguageLabel(code) })),
    ]
  }, [capabilities, model, availableModels])

  // PII settings
  const [piiDetection, setPiiDetection] = useState(false)
  const [piiTier, setPiiTier] = useState<PIITier>('standard')
  const [piiEntityTypes, setPiiEntityTypes] = useState('')
  const [redactPiiAudio, setRedactPiiAudio] = useState(false)
  const [piiRedactionMode, setPiiRedactionMode] = useState<PIIRedactionMode>('silence')

  // Form state
  const [error, setError] = useState<string | null>(null)
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({})

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setIsDragOver(true)
  }, [])

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setIsDragOver(false)
  }, [])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setIsDragOver(false)
    const droppedFile = e.dataTransfer.files[0]
    if (droppedFile) {
      setFile(droppedFile)
      setFieldErrors((prev) => ({ ...prev, source: '' }))
    }
  }, [])

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFile = e.target.files?.[0]
    if (selectedFile) {
      setFile(selectedFile)
      setFieldErrors((prev) => ({ ...prev, source: '' }))
    }
  }

  const handleRemoveFile = () => {
    setFile(null)
    if (fileInputRef.current) {
      fileInputRef.current.value = ''
    }
  }

  const validate = (): boolean => {
    const errors: Record<string, string> = {}

    // Source validation
    if (sourceType === 'file' && !file) {
      errors.source = 'Please select an audio file.'
    }
    if (sourceType === 'url') {
      if (!audioUrl.trim()) {
        errors.source = 'Please enter an audio URL.'
      } else {
        try {
          new URL(audioUrl)
        } catch {
          errors.source = 'Invalid URL format.'
        }
      }
    }

    // Speaker settings validation
    if (speakerDetection !== 'none') {
      const num = numSpeakers ? parseInt(numSpeakers, 10) : undefined
      const min = minSpeakers ? parseInt(minSpeakers, 10) : undefined
      const max = maxSpeakers ? parseInt(maxSpeakers, 10) : undefined

      if (num !== undefined && (isNaN(num) || num < 1 || num > 32)) {
        errors.numSpeakers = 'Must be between 1 and 32.'
      }
      if (min !== undefined && (isNaN(min) || min < 1 || min > 32)) {
        errors.minSpeakers = 'Must be between 1 and 32.'
      }
      if (max !== undefined && (isNaN(max) || max < 1 || max > 32)) {
        errors.maxSpeakers = 'Must be between 1 and 32.'
      }
      if (min !== undefined && max !== undefined && min > max) {
        errors.minSpeakers = 'Min must be less than or equal to max.'
      }
    }

    // Vocabulary validation
    if (vocabulary.trim()) {
      const terms = vocabulary.split(',').map((t) => t.trim()).filter(Boolean)
      if (terms.length > 100) {
        errors.vocabulary = 'Maximum 100 vocabulary terms allowed.'
      }
    }

    setFieldErrors(errors)
    return Object.keys(errors).length === 0
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)

    if (!validate()) {
      return
    }

    try {
      const result = await createJob.mutateAsync({
        file: sourceType === 'file' ? file ?? undefined : undefined,
        audio_url: sourceType === 'url' ? audioUrl.trim() : undefined,
        language: language !== 'auto' ? language : undefined,
        speaker_detection: speakerDetection,
        num_speakers: numSpeakers ? parseInt(numSpeakers, 10) : undefined,
        min_speakers: minSpeakers ? parseInt(minSpeakers, 10) : undefined,
        max_speakers: maxSpeakers ? parseInt(maxSpeakers, 10) : undefined,
        timestamps_granularity: timestampsGranularity,
        model: model !== 'auto' ? model : undefined,
        vocabulary: vocabulary.trim()
          ? vocabulary.split(',').map((t) => t.trim()).filter(Boolean)
          : undefined,
        retention_policy:
          retentionMode === 'default'
            ? undefined
            : retentionMode === 'transient'
              ? '0'
              : retentionMode === 'permanent'
                ? '-1'
                : retentionDays,
        pii_detection: piiDetection || undefined,
        pii_detection_tier: piiDetection ? piiTier : undefined,
        pii_entity_types: piiDetection && piiEntityTypes.trim()
          ? piiEntityTypes.split(',').map((t) => t.trim()).filter(Boolean)
          : undefined,
        redact_pii_audio: piiDetection ? redactPiiAudio : undefined,
        pii_redaction_mode: piiDetection && redactPiiAudio ? piiRedactionMode : undefined,
      })

      navigate(`/jobs/${result.id}`)
    } catch (err) {
      if (err instanceof Error) {
        setError(err.message)
      } else {
        setError('Failed to submit job. Please try again.')
      }
    }
  }

  const handleCancel = () => {
    navigate('/jobs')
  }

  const showSpeakerOptions = speakerDetection !== 'none'

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex items-center gap-4">
        <BackButton fallbackPath="/jobs" label="Back to Jobs" variant="link" />
      </div>
      <div>
        <h1 className="text-2xl font-bold">Submit Batch Job</h1>
        <p className="text-muted-foreground">
          Upload audio or provide an audio URL to create a transcription job.
        </p>
      </div>

      <form onSubmit={handleSubmit}>
        <div className={isMobile ? 'space-y-6' : 'grid grid-cols-3 gap-6'}>
          {/* Main Form (left column on desktop) */}
          <div className={isMobile ? 'space-y-6' : 'col-span-2 space-y-6'}>
            {/* Source Card */}
            <Card>
              <CardHeader>
                <CardTitle className="text-base font-medium">Audio Source</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                {/* Source Type Segmented Control */}
                <div className="flex rounded-md border border-input overflow-hidden">
                  <button
                    type="button"
                    className={`flex-1 flex items-center justify-center gap-2 px-4 py-2 text-sm font-medium transition-colors ${
                      sourceType === 'file'
                        ? 'bg-primary text-primary-foreground'
                        : 'bg-background hover:bg-accent'
                    }`}
                    onClick={() => setSourceType('file')}
                  >
                    <Upload className="h-4 w-4" />
                    Upload File
                  </button>
                  <button
                    type="button"
                    className={`flex-1 flex items-center justify-center gap-2 px-4 py-2 text-sm font-medium transition-colors ${
                      sourceType === 'url'
                        ? 'bg-primary text-primary-foreground'
                        : 'bg-background hover:bg-accent'
                    }`}
                    onClick={() => setSourceType('url')}
                  >
                    <Link className="h-4 w-4" />
                    Audio URL
                  </button>
                </div>

                {/* File Upload */}
                {sourceType === 'file' && (
                  <div className="space-y-2">
                    {!file ? (
                      <div
                        className={`border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors ${
                          isDragOver
                            ? 'border-primary bg-primary/5'
                            : 'border-input hover:border-muted-foreground'
                        }`}
                        onDragOver={handleDragOver}
                        onDragLeave={handleDragLeave}
                        onDrop={handleDrop}
                        onClick={() => fileInputRef.current?.click()}
                      >
                        <Upload className="h-8 w-8 mx-auto mb-2 text-muted-foreground" />
                        <p className="text-sm font-medium">
                          Drop your audio file here or click to browse
                        </p>
                        <p className="text-xs text-muted-foreground mt-1">
                          Supported formats include MP3, WAV, FLAC, OGG, and M4A.
                        </p>
                        <input
                          ref={fileInputRef}
                          type="file"
                          accept="audio/*"
                          onChange={handleFileSelect}
                          className="hidden"
                        />
                      </div>
                    ) : (
                      <div className="flex items-center justify-between p-3 rounded-md border border-input bg-accent/50">
                        <div className="flex items-center gap-3 min-w-0">
                          <Upload className="h-5 w-5 text-muted-foreground flex-shrink-0" />
                          <div className="min-w-0">
                            <p className="text-sm font-medium truncate">{file.name}</p>
                            <p className="text-xs text-muted-foreground">
                              {(file.size / 1024 / 1024).toFixed(2)} MB
                            </p>
                          </div>
                        </div>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={handleRemoveFile}
                        >
                          <X className="h-4 w-4" />
                        </Button>
                      </div>
                    )}
                  </div>
                )}

                {/* Audio URL */}
                {sourceType === 'url' && (
                  <div className="space-y-2">
                    <label htmlFor="audioUrl" className="text-sm font-medium">
                      Audio URL
                    </label>
                    <input
                      id="audioUrl"
                      type="url"
                      value={audioUrl}
                      onChange={(e) => {
                        setAudioUrl(e.target.value)
                        setFieldErrors((prev) => ({ ...prev, source: '' }))
                      }}
                      placeholder="https://example.com/audio.mp3"
                      className="w-full px-3 py-2 rounded-md border border-input bg-background text-sm"
                    />
                    <p className="text-xs text-muted-foreground">
                      Use a direct HTTPS or presigned URL to an audio file.
                    </p>
                  </div>
                )}

                {fieldErrors.source && (
                  <p className="text-sm text-destructive flex items-center gap-1">
                    <AlertCircle className="h-4 w-4" />
                    {fieldErrors.source}
                  </p>
                )}
              </CardContent>
            </Card>

            {/* Basic Settings Card */}
            <Card>
              <CardHeader>
                <CardTitle className="text-base font-medium">Basic Settings</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid gap-4 md:grid-cols-3">
                  {/* Language */}
                  <div className="space-y-2">
                    <label className="text-sm font-medium">Language</label>
                    <Select value={language} onValueChange={setLanguage}>
                      <SelectTrigger>
                        <SelectValue placeholder="Select language" />
                      </SelectTrigger>
                      <SelectContent>
                        {languageOptions.map((opt) => (
                          <SelectItem key={opt.value} value={opt.value}>
                            {opt.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>

                  {/* Speaker Detection */}
                  <div className="space-y-2">
                    <label className="text-sm font-medium">Speaker Detection</label>
                    <Select
                      value={speakerDetection}
                      onValueChange={(v) => setSpeakerDetection(v as SpeakerDetection)}
                    >
                      <SelectTrigger>
                        <SelectValue placeholder="Select mode" />
                      </SelectTrigger>
                      <SelectContent>
                        {SPEAKER_DETECTION_OPTIONS.map((opt) => (
                          <SelectItem key={opt.value} value={opt.value}>
                            {opt.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>

                  {/* Timestamps */}
                  <div className="space-y-2">
                    <label className="text-sm font-medium">Timestamps</label>
                    <Select
                      value={timestampsGranularity}
                      onValueChange={(v) => setTimestampsGranularity(v as TimestampsGranularity)}
                    >
                      <SelectTrigger>
                        <SelectValue placeholder="Select granularity" />
                      </SelectTrigger>
                      <SelectContent>
                        {TIMESTAMPS_OPTIONS.map((opt) => (
                          <SelectItem key={opt.value} value={opt.value}>
                            {opt.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </div>

                {/* Number of Speakers (conditional) */}
                {showSpeakerOptions && (
                  <div className="pt-2">
                    <div className="space-y-2 max-w-[200px]">
                      <label htmlFor="numSpeakers" className="text-sm font-medium">
                        Number of Speakers
                      </label>
                      <input
                        id="numSpeakers"
                        type="number"
                        min="1"
                        max="32"
                        value={numSpeakers}
                        onChange={(e) => setNumSpeakers(e.target.value)}
                        placeholder="Auto-detect"
                        className="w-full px-3 py-2 rounded-md border border-input bg-background text-sm"
                      />
                      <p className="text-xs text-muted-foreground">
                        Leave empty to auto-detect, or set exact count (1-32).
                      </p>
                      {fieldErrors.numSpeakers && (
                        <p className="text-xs text-destructive">{fieldErrors.numSpeakers}</p>
                      )}
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Advanced Settings Accordion */}
            <Card>
              <CardHeader
                className="cursor-pointer"
                onClick={() => setShowAdvanced(!showAdvanced)}
              >
                <div className="flex items-center justify-between">
                  <CardTitle className="text-base font-medium">Advanced Settings</CardTitle>
                  {showAdvanced ? (
                    <ChevronUp className="h-5 w-5 text-muted-foreground" />
                  ) : (
                    <ChevronDown className="h-5 w-5 text-muted-foreground" />
                  )}
                </div>
                {!showAdvanced && (
                  <p className="text-xs text-muted-foreground mt-1">
                    Leave fields at defaults unless you need explicit control.
                  </p>
                )}
              </CardHeader>
              {showAdvanced && (
                <CardContent className="space-y-4 pt-0">
                  <div className="grid gap-4 md:grid-cols-2">
                    {/* Model */}
                    <div className="space-y-2">
                      <label className="text-sm font-medium">Model</label>
                      <Select value={model} onValueChange={setModel}>
                        <SelectTrigger>
                          <SelectValue placeholder="Select model" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="auto">Auto</SelectItem>
                          {availableModels.map((m) => (
                            <SelectItem key={m.id} value={m.id}>
                              {m.name}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>

                    {/* Retention Policy */}
                    <div className="space-y-2">
                      <label className="text-sm font-medium">Retention Policy</label>
                      <div className="flex gap-2">
                        <Select
                          value={retentionMode}
                          onValueChange={(v) => setRetentionMode(v as RetentionMode)}
                        >
                          <SelectTrigger className="w-[180px]">
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            {RETENTION_OPTIONS.map((opt) => (
                              <SelectItem key={opt.value} value={opt.value}>
                                {opt.label}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                        {retentionMode === 'days' && (
                          <div className="flex items-center gap-2">
                            <input
                              type="number"
                              min="1"
                              max="3650"
                              value={retentionDays}
                              onChange={(e) => setRetentionDays(e.target.value)}
                              className="w-20 px-3 py-2 rounded-md border border-input bg-background text-sm"
                            />
                            <span className="text-sm text-muted-foreground">days</span>
                          </div>
                        )}
                      </div>
                    </div>
                  </div>

                  {/* Speaker Range (for diarization) */}
                  {showSpeakerOptions && (
                    <div className="grid gap-4 md:grid-cols-2">
                      <div className="space-y-2">
                        <label htmlFor="minSpeakers" className="text-sm font-medium">
                          Min Speakers
                        </label>
                        <input
                          id="minSpeakers"
                          type="number"
                          min="1"
                          max="32"
                          value={minSpeakers}
                          onChange={(e) => setMinSpeakers(e.target.value)}
                          placeholder="Optional"
                          className="w-full px-3 py-2 rounded-md border border-input bg-background text-sm"
                        />
                        {fieldErrors.minSpeakers && (
                          <p className="text-xs text-destructive">{fieldErrors.minSpeakers}</p>
                        )}
                      </div>
                      <div className="space-y-2">
                        <label htmlFor="maxSpeakers" className="text-sm font-medium">
                          Max Speakers
                        </label>
                        <input
                          id="maxSpeakers"
                          type="number"
                          min="1"
                          max="32"
                          value={maxSpeakers}
                          onChange={(e) => setMaxSpeakers(e.target.value)}
                          placeholder="Optional"
                          className="w-full px-3 py-2 rounded-md border border-input bg-background text-sm"
                        />
                        {fieldErrors.maxSpeakers && (
                          <p className="text-xs text-destructive">{fieldErrors.maxSpeakers}</p>
                        )}
                      </div>
                    </div>
                  )}

                  {/* Vocabulary */}
                  <div className="space-y-2">
                    <label htmlFor="vocabulary" className="text-sm font-medium">
                      Vocabulary
                    </label>
                    <textarea
                      id="vocabulary"
                      value={vocabulary}
                      onChange={(e) => setVocabulary(e.target.value)}
                      placeholder="Enter comma-separated terms (e.g., Kubernetes, PostgreSQL, FastAPI)"
                      rows={2}
                      className="w-full px-3 py-2 rounded-md border border-input bg-background text-sm resize-none"
                    />
                    <p className="text-xs text-muted-foreground">
                      Custom vocabulary helps with domain-specific terms. Maximum 100 terms.
                    </p>
                    {fieldErrors.vocabulary && (
                      <p className="text-xs text-destructive">{fieldErrors.vocabulary}</p>
                    )}
                  </div>

                  {/* PII Detection Section */}
                  <div className="border-t border-border pt-4 mt-4">
                    <div className="flex items-center justify-between mb-4">
                      <div>
                        <label className="text-sm font-medium">PII Detection</label>
                        <p className="text-xs text-muted-foreground">
                          Detect and optionally redact personally identifiable information
                        </p>
                      </div>
                      <button
                        type="button"
                        role="switch"
                        aria-checked={piiDetection}
                        onClick={() => setPiiDetection(!piiDetection)}
                        className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                          piiDetection ? 'bg-primary' : 'bg-input'
                        }`}
                      >
                        <span
                          className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                            piiDetection ? 'translate-x-6' : 'translate-x-1'
                          }`}
                        />
                      </button>
                    </div>

                    {piiDetection && (
                      <div className="space-y-4 pl-0 md:pl-4">
                        <div className="grid gap-4 md:grid-cols-2">
                          <div className="space-y-2">
                            <label className="text-sm font-medium">Detection Tier</label>
                            <Select
                              value={piiTier}
                              onValueChange={(v) => setPiiTier(v as PIITier)}
                            >
                              <SelectTrigger>
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                {PII_TIER_OPTIONS.map((opt) => (
                                  <SelectItem key={opt.value} value={opt.value}>
                                    {opt.label}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </div>

                          <div className="space-y-2">
                            <label htmlFor="piiEntityTypes" className="text-sm font-medium">
                              Entity Types
                            </label>
                            <input
                              id="piiEntityTypes"
                              type="text"
                              value={piiEntityTypes}
                              onChange={(e) => setPiiEntityTypes(e.target.value)}
                              placeholder="All (default)"
                              className="w-full px-3 py-2 rounded-md border border-input bg-background text-sm"
                            />
                          </div>
                        </div>

                        <div className="flex items-center justify-between">
                          <div>
                            <label className="text-sm font-medium">Redact PII in Audio</label>
                            <p className="text-xs text-muted-foreground">
                              Generate a redacted version of the audio file
                            </p>
                          </div>
                          <button
                            type="button"
                            role="switch"
                            aria-checked={redactPiiAudio}
                            onClick={() => setRedactPiiAudio(!redactPiiAudio)}
                            className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                              redactPiiAudio ? 'bg-primary' : 'bg-input'
                            }`}
                          >
                            <span
                              className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                                redactPiiAudio ? 'translate-x-6' : 'translate-x-1'
                              }`}
                            />
                          </button>
                        </div>

                        {redactPiiAudio && (
                          <div className="space-y-2">
                            <label className="text-sm font-medium">Redaction Mode</label>
                            <Select
                              value={piiRedactionMode}
                              onValueChange={(v) => setPiiRedactionMode(v as PIIRedactionMode)}
                            >
                              <SelectTrigger className="w-full md:w-48">
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                {PII_REDACTION_MODE_OPTIONS.map((opt) => (
                                  <SelectItem key={opt.value} value={opt.value}>
                                    {opt.label}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                </CardContent>
              )}
            </Card>

            {/* Form Error */}
            {error && (
              <div className="flex items-start gap-2 p-4 rounded-md bg-destructive/10 text-destructive">
                <AlertCircle className="h-5 w-5 flex-shrink-0 mt-0.5" />
                <div>
                  <p className="text-sm font-medium">Submission failed</p>
                  <p className="text-sm">{error}</p>
                </div>
              </div>
            )}

            {/* Actions (desktop) */}
            {!isMobile && (
              <div className="flex justify-end gap-3">
                <Button type="button" variant="outline" onClick={handleCancel}>
                  Cancel
                </Button>
                <Button type="submit" disabled={createJob.isPending}>
                  {createJob.isPending ? 'Submitting...' : 'Submit Job'}
                </Button>
              </div>
            )}
          </div>

          {/* Sidebar (right column on desktop) */}
          {!isMobile && (
            <div className="space-y-6">
              {/* Summary Card */}
              <Card>
                <CardHeader>
                  <CardTitle className="text-base font-medium">Summary</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3 text-sm">
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Source</span>
                    <span>
                      {sourceType === 'file'
                        ? file
                          ? file.name.slice(0, 20) + (file.name.length > 20 ? '...' : '')
                          : 'No file selected'
                        : audioUrl
                          ? 'URL provided'
                          : 'No URL'}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Language</span>
                    <span>{languageOptions.find((l) => l.value === language)?.label || language}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Speaker Detection</span>
                    <span>
                      {SPEAKER_DETECTION_OPTIONS.find((s) => s.value === speakerDetection)?.label}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Timestamps</span>
                    <span>
                      {TIMESTAMPS_OPTIONS.find((t) => t.value === timestampsGranularity)?.label}
                    </span>
                  </div>
                  {piiDetection && (
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">PII Detection</span>
                      <span>Enabled ({piiTier})</span>
                    </div>
                  )}
                </CardContent>
              </Card>

              {/* Guidance Card */}
              <Card>
                <CardHeader>
                  <CardTitle className="text-base font-medium flex items-center gap-2">
                    <Info className="h-4 w-4" />
                    Tips
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-3 text-sm text-muted-foreground">
                  <p>
                    For best results, use high-quality audio with minimal background noise.
                  </p>
                  <p>
                    Speaker diarization works best with 2-10 speakers and clear turn-taking.
                  </p>
                  <p>
                    Add domain-specific terms to vocabulary for improved accuracy.
                  </p>
                </CardContent>
              </Card>
            </div>
          )}
        </div>

        {/* Mobile Sticky Bottom Bar */}
        {isMobile && (
          <div className="fixed bottom-0 left-0 right-0 p-4 bg-background border-t border-border">
            <div className="flex gap-3">
              <Button
                type="button"
                variant="outline"
                className="flex-1"
                onClick={handleCancel}
              >
                Cancel
              </Button>
              <Button type="submit" className="flex-1" disabled={createJob.isPending}>
                {createJob.isPending ? 'Submitting...' : 'Submit Job'}
              </Button>
            </div>
          </div>
        )}
      </form>

      {/* Mobile bottom spacing for sticky bar */}
      {isMobile && <div className="h-20" />}
    </div>
  )
}
