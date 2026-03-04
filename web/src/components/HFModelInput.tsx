import { useState, useEffect, useRef } from 'react'
import { Loader2, Check, AlertTriangle, Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import type { HFResolveResponse } from '@/api/types'

// Format large numbers with K/M suffix
function formatNumber(num: number | undefined): string {
  if (num === undefined || num === 0) return '0'
  if (num >= 1_000_000) return `${(num / 1_000_000).toFixed(1)}M`
  if (num >= 1_000) return `${(num / 1_000).toFixed(1)}K`
  return num.toString()
}

interface HFSearchResult {
  id: string
  downloads: number
  likes: number
}

interface HFModelInputProps {
  onResolve: (modelId: string) => void
  isLoading: boolean
  result?: HFResolveResponse
  error?: Error | null
  autoFocus?: boolean
}

export function HFModelInput({ onResolve, isLoading, result, error, autoFocus }: HFModelInputProps) {
  const [modelId, setModelId] = useState('')
  const [suggestions, setSuggestions] = useState<HFSearchResult[]>([])
  const [isSearching, setIsSearching] = useState(false)
  const [showSuggestions, setShowSuggestions] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const suggestionsRef = useRef<HTMLDivElement>(null)

  // Search HuggingFace Hub for ASR models
  useEffect(() => {
    const query = modelId.trim()
    if (query.length < 2) {
      setSuggestions([])
      return
    }

    const controller = new AbortController()
    const timeoutId = setTimeout(async () => {
      setIsSearching(true)
      try {
        // Search HuggingFace Hub API for ASR models
        const response = await fetch(
          `https://huggingface.co/api/models?search=${encodeURIComponent(query)}&pipeline_tag=automatic-speech-recognition&sort=downloads&direction=-1&limit=8`,
          { signal: controller.signal }
        )
        if (response.ok) {
          const data = await response.json()
          setSuggestions(
            data.map((m: { id: string; downloads: number; likes: number }) => ({
              id: m.id,
              downloads: m.downloads,
              likes: m.likes,
            }))
          )
          setShowSuggestions(true)
        }
      } catch (e) {
        if ((e as Error).name !== 'AbortError') {
          console.error('HF search failed:', e)
        }
      } finally {
        setIsSearching(false)
      }
    }, 300) // Debounce 300ms

    return () => {
      clearTimeout(timeoutId)
      controller.abort()
    }
  }, [modelId])

  // Focus input when autoFocus is true (for dialog usage)
  useEffect(() => {
    if (autoFocus) {
      // Delay to ensure dialog animation completes
      const timer = setTimeout(() => {
        inputRef.current?.focus()
      }, 150)
      return () => clearTimeout(timer)
    }
  }, [autoFocus])

  // Close suggestions when clicking outside
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (
        suggestionsRef.current &&
        !suggestionsRef.current.contains(event.target as Node) &&
        inputRef.current &&
        !inputRef.current.contains(event.target as Node)
      ) {
        setShowSuggestions(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const handleSelect = (id: string) => {
    setModelId(id)
    setSuggestions([])
    setShowSuggestions(false)
    onResolve(id)
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const trimmed = modelId.trim()
    if (!trimmed) return
    setShowSuggestions(false)
    onResolve(trimmed)
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      <div className="flex gap-2">
        <div className="relative flex-1">
          <input
            ref={inputRef}
            type="text"
            placeholder="parakeet, whisper, ..."
            value={modelId}
            onChange={(e) => setModelId(e.target.value)}
            onFocus={() => suggestions.length > 0 && setShowSuggestions(true)}
            className="w-full h-10 px-3 rounded-md border border-input bg-background text-sm"
          />
          {isSearching && (
            <div className="absolute right-3 top-1/2 -translate-y-1/2">
              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
            </div>
          )}

          {/* Suggestions dropdown */}
          {showSuggestions && suggestions.length > 0 && (
            <div
              ref={suggestionsRef}
              className="absolute z-50 w-full mt-1 py-1 bg-popover border border-border rounded-md shadow-lg max-h-64 overflow-auto"
            >
              {suggestions.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => handleSelect(s.id)}
                  className="w-full px-3 py-2 text-left text-sm hover:bg-accent flex items-center justify-between gap-2"
                >
                  <span className="truncate font-medium">{s.id}</span>
                  <span className="text-xs text-muted-foreground flex-shrink-0">
                    {formatNumber(s.downloads)} downloads
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
        <Button type="submit" disabled={isLoading || !modelId.trim()}>
          {isLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <><Plus className="h-4 w-4 mr-1" />Add</>}
        </Button>
      </div>

      <p className="text-xs text-muted-foreground">
        Type to search HuggingFace Hub for ASR models, or enter a full model ID (e.g., nvidia/parakeet-tdt-1.1b)
      </p>

      {result && (
        <div
          className={cn(
            'p-3 rounded-lg text-sm',
            result.can_route
              ? 'bg-green-500/10 border border-green-500/20'
              : 'bg-yellow-500/10 border border-yellow-500/20'
          )}
        >
          {result.can_route ? (
            <div className="space-y-1">
              <p className="font-medium text-green-600 dark:text-green-400 flex items-center gap-2">
                <Check className="h-4 w-4" />
                Resolved to runtime: {result.resolved_runtime}
              </p>
              {result.library_name && (
                <p className="text-muted-foreground">Library: {result.library_name}</p>
              )}
              {result.languages.length > 0 && (
                <p className="text-muted-foreground">
                  Languages: {result.languages.slice(0, 10).join(', ')}
                  {result.languages.length > 10 && ` +${result.languages.length - 10} more`}
                </p>
              )}
              <p className="text-muted-foreground">
                {formatNumber(result.downloads)} downloads • {formatNumber(result.likes)} likes
              </p>
              <p className="text-xs text-muted-foreground mt-2">
                Model has been added to the registry. You can now pull it.
              </p>
            </div>
          ) : (
            <p className="text-yellow-600 dark:text-yellow-400 flex items-center gap-2">
              <AlertTriangle className="h-4 w-4" />
              Could not determine runtime for this model. It may not be a supported ASR model.
              {result.error && <span className="block text-xs mt-1">{result.error}</span>}
            </p>
          )}
        </div>
      )}

      {error && (
        <p className="text-sm text-red-500 flex items-center gap-2">
          <AlertTriangle className="h-4 w-4" />
          Error: {error.message}
        </p>
      )}
    </form>
  )
}
