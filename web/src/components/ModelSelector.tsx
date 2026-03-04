import { useState, useMemo, useRef, useEffect } from 'react'
import { Search, ChevronDown, Check, Sparkles, AlertCircle, Loader2 } from 'lucide-react'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { useModelRegistry } from '@/hooks/useModelRegistry'
import { cn } from '@/lib/utils'
import type { ModelRegistryEntry } from '@/api/types'

function formatBytes(bytes: number | null): string {
  if (bytes === null) return ''
  const gb = bytes / (1024 * 1024 * 1024)
  if (gb >= 1) return `${gb.toFixed(1)} GB`
  const mb = bytes / (1024 * 1024)
  return `${mb.toFixed(0)} MB`
}

interface ModelSelectorProps {
  value: string
  onChange: (value: string) => void
  language?: string // Filter models by language compatibility
}

export function ModelSelector({ value, onChange, language }: ModelSelectorProps) {
  const [open, setOpen] = useState(false)
  const [userInput, setUserInput] = useState('') // What the user actually typed
  const [highlightedIndex, setHighlightedIndex] = useState(0)
  const searchInputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  const { data: registryData, isLoading } = useModelRegistry({ stage: 'transcribe' })

  // Focus search input when dialog opens
  useEffect(() => {
    if (open) {
      // Small delay to ensure dialog is mounted
      const timer = setTimeout(() => {
        searchInputRef.current?.focus()
      }, 0)
      return () => clearTimeout(timer)
    }
  }, [open])

  // Handle dialog open/close with state reset
  const handleOpenChange = (newOpen: boolean) => {
    setOpen(newOpen)
    if (!newOpen) {
      // Reset state when dialog closes
      setHighlightedIndex(0)
      setUserInput('')
    }
  }

  const models = useMemo(() => registryData?.data ?? [], [registryData?.data])

  // Filter to ready models only, with optional search and language filter
  const readyModels = useMemo(() => {
    return models.filter((m) => {
      // Only show ready models
      if (m.status !== 'ready') return false

      // Filter by userInput
      if (userInput) {
        const searchLower = userInput.toLowerCase()
        const matches =
          m.id.toLowerCase().includes(searchLower) ||
          m.name?.toLowerCase().includes(searchLower) ||
          m.runtime.toLowerCase().includes(searchLower)
        if (!matches) return false
      }

      // Filter by language if specified
      if (language && language !== 'auto' && m.languages && m.languages.length > 0) {
        if (!m.languages.includes(language)) return false
      }

      return true
    })
  }, [models, userInput, language])

  // Compute inline completion from first match (Spotlight-style)
  const completion = useMemo(() => {
    if (!userInput || readyModels.length === 0) return ''
    const firstMatch = readyModels[0]
    const name = (firstMatch.name || firstMatch.id).toLowerCase()
    const input = userInput.toLowerCase()
    if (name.startsWith(input)) {
      // Return the suffix with original casing
      return (firstMatch.name || firstMatch.id).slice(userInput.length)
    }
    return ''
  }, [userInput, readyModels])

  // Build flat list of selectable items: auto (only when no search) + filtered models
  const selectableIds = useMemo(() => {
    if (userInput) {
      return readyModels.map((m) => m.id)
    }
    return ['auto', ...readyModels.map((m) => m.id)]
  }, [readyModels, userInput])


  // Find the selected model for display
  const selectedModel = models.find((m) => m.id === value)

  const handleSelect = (modelId: string) => {
    onChange(modelId)
    setOpen(false)
    setUserInput('')
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    switch (e.key) {
      case 'ArrowDown':
        e.preventDefault()
        setHighlightedIndex((prev) => (prev + 1) % selectableIds.length)
        break
      case 'ArrowUp':
        e.preventDefault()
        setHighlightedIndex((prev) => (prev - 1 + selectableIds.length) % selectableIds.length)
        break
      case 'Enter':
        e.preventDefault()
        if (selectableIds[highlightedIndex]) {
          handleSelect(selectableIds[highlightedIndex])
        }
        break
      case 'Tab':
        // Accept the completion
        if (completion) {
          e.preventDefault()
          setUserInput(userInput + completion)
        }
        break
      case 'ArrowRight':
        // Accept completion if cursor is at the end
        if (completion && searchInputRef.current) {
          const cursorPos = searchInputRef.current.selectionStart
          if (cursorPos === userInput.length) {
            e.preventDefault()
            setUserInput(userInput + completion)
          }
        }
        break
      case 'Escape':
        e.preventDefault()
        e.stopPropagation() // Prevent Dialog from closing
        if (userInput) {
          setUserInput('')
        } else {
          setOpen(false)
        }
        break
    }
  }

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setUserInput(e.target.value)
    setHighlightedIndex(0) // Reset highlight when search changes
  }

  // Scroll highlighted item into view
  useEffect(() => {
    if (!listRef.current) return
    const highlighted = listRef.current.querySelector('[data-highlighted="true"]')
    if (highlighted) {
      highlighted.scrollIntoView({ block: 'nearest' })
    }
  }, [highlightedIndex])

  return (
    <>
      {/* Trigger Button */}
      <Button
        type="button"
        variant="outline"
        role="combobox"
        aria-expanded={open}
        className="w-full justify-between font-normal"
        onClick={() => setOpen(true)}
      >
        <span className="flex items-center gap-2 truncate">
          {value === 'auto' ? (
            <>
              <Sparkles className="h-4 w-4 text-muted-foreground shrink-0" />
              <span>Auto</span>
            </>
          ) : selectedModel ? (
            <>
              <Badge variant="secondary" className="text-xs shrink-0">
                {selectedModel.runtime}
              </Badge>
              <span className="truncate">{selectedModel.name || selectedModel.id}</span>
            </>
          ) : (
            <span className="text-muted-foreground">Select model...</span>
          )}
        </span>
        <ChevronDown className="h-4 w-4 shrink-0 opacity-50" />
      </Button>

      {/* Model Selection Dialog */}
      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent className="max-w-[500px] sm:min-w-[500px]">
          <div className="bg-card rounded-lg border shadow-lg max-h-[70vh] flex flex-col">
            {/* Header with Search */}
            <div className="p-3 border-b">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground z-10" />
                {/* Ghost text showing completion */}
                <div className="absolute inset-0 pl-9 pr-3 py-2 text-sm pointer-events-none flex items-center">
                  <span className="invisible">{userInput}</span>
                  <span className="text-muted-foreground/50">{completion}</span>
                </div>
                <input
                  ref={searchInputRef}
                  type="text"
                  placeholder={userInput ? '' : 'Search models...'}
                  value={userInput}
                  onChange={handleInputChange}
                  onKeyDown={handleKeyDown}
                  className="w-full pl-9 pr-3 py-2 text-sm rounded-md border bg-transparent focus:outline-none focus:ring-2 focus:ring-ring relative"
                />
              </div>
            </div>

            {/* Model List */}
            <div ref={listRef} className="flex-1 overflow-y-auto p-2">
              {isLoading ? (
                <div className="flex items-center justify-center py-8">
                  <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
                </div>
              ) : (
                <>
                  {/* Auto Option - only show when not searching */}
                  {!userInput && (
                    <div className="mb-2">
                      <div className="px-2 py-1.5 text-xs font-medium text-muted-foreground">
                        Recommended
                      </div>
                      <button
                        type="button"
                        data-highlighted={highlightedIndex === 0}
                        className={cn(
                          'w-full flex items-center gap-3 px-3 py-2 rounded-md text-left transition-colors',
                          'hover:bg-accent',
                          highlightedIndex === 0 && 'bg-accent',
                          value === 'auto' && highlightedIndex !== 0 && 'bg-accent/50'
                        )}
                        onClick={() => handleSelect('auto')}
                      >
                        <Sparkles className="h-4 w-4 text-muted-foreground shrink-0" />
                        <div className="flex-1 min-w-0">
                          <div className="font-medium">Auto</div>
                          <div className="text-xs text-muted-foreground">
                            Automatically select the best model for your audio
                          </div>
                        </div>
                        {value === 'auto' && <Check className="h-4 w-4 shrink-0" />}
                      </button>
                    </div>
                  )}

                  {/* Ready Models */}
                  {readyModels.length > 0 && (
                    <div className="mb-2">
                      <div className="px-2 py-1.5 text-xs font-medium text-muted-foreground">
                        Downloaded Models
                      </div>
                      {readyModels.map((model, idx) => (
                        <ModelOption
                          key={model.id}
                          model={model}
                          isSelected={value === model.id}
                          isHighlighted={highlightedIndex === (userInput ? idx : idx + 1)}
                          onSelect={() => handleSelect(model.id)}
                        />
                      ))}
                    </div>
                  )}

                  {/* Empty state */}
                  {readyModels.length === 0 && (
                    userInput ? (
                      <div className="py-6 text-center text-sm text-muted-foreground">
                        No downloaded models match "{userInput}"
                      </div>
                    ) : (
                      <div className="py-6 text-center text-sm text-muted-foreground">
                        <AlertCircle className="h-5 w-5 mx-auto mb-2 opacity-50" />
                        No models downloaded
                        <p className="text-xs mt-1">
                          Download models from the{' '}
                          <a href="/models" className="underline hover:text-foreground">
                            Models page
                          </a>
                        </p>
                      </div>
                    )
                  )}

                  {/* Help link - show when not searching and models exist */}
                  {!userInput && readyModels.length > 0 && (
                    <div className="border-t mt-2 pt-2 px-3 pb-1">
                      <p className="text-xs text-muted-foreground">
                        Register more models on the{' '}
                        <a href="/models" className="underline hover:text-foreground">
                          Models page
                        </a>
                      </p>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}

interface ModelOptionProps {
  model: ModelRegistryEntry
  isSelected: boolean
  isHighlighted: boolean
  onSelect: () => void
}

function ModelOption({ model, isSelected, isHighlighted, onSelect }: ModelOptionProps) {
  return (
    <button
      type="button"
      data-highlighted={isHighlighted}
      className={cn(
        'w-full flex items-start gap-3 px-3 py-2 rounded-md text-left transition-colors',
        'hover:bg-accent',
        isHighlighted && 'bg-accent',
        isSelected && !isHighlighted && 'bg-accent/50'
      )}
      onClick={onSelect}
    >
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="font-medium truncate">{model.name || model.id}</span>
          {model.size_bytes && (
            <span className="text-xs text-muted-foreground shrink-0">
              {formatBytes(model.size_bytes)}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground mt-0.5">
          <Badge variant="outline" className="text-[10px] px-1 py-0">
            {model.runtime}
          </Badge>
          {model.languages && model.languages.length > 0 && (
            <span>
              {model.languages.length > 5
                ? `${model.languages.length} langs`
                : model.languages.slice(0, 3).join(', ')}
            </span>
          )}
          {model.word_timestamps && <span>• timestamps</span>}
        </div>
      </div>
      {isSelected && <Check className="h-4 w-4 shrink-0 mt-1" />}
    </button>
  )
}
