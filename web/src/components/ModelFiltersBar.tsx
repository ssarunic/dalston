import { Search, X } from 'lucide-react'
import { S } from '@/lib/strings'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Button } from '@/components/ui/button'
import type { ModelFilters, ModelStatus } from '@/api/types'

interface ModelFiltersBarProps {
  filters: ModelFilters
  onChange: (filters: ModelFilters) => void
}

const STAGES = [
  { value: 'transcribe', label: 'Transcribe' },
  { value: 'align', label: 'Align' },
  { value: 'diarize', label: 'Diarize' },
]

const RUNTIMES = [
  { value: 'faster-whisper', label: 'Faster Whisper' },
  { value: 'nemo', label: 'NeMo' },
  { value: 'whisperx', label: 'WhisperX' },
  { value: 'hf-asr', label: 'HuggingFace ASR' },
  { value: 'pyannote', label: 'Pyannote' },
]

const STATUSES = [
  { value: 'ready', label: 'Ready' },
  { value: 'downloading', label: 'Downloading' },
  { value: 'not_downloaded', label: 'Not Downloaded' },
  { value: 'failed', label: 'Failed' },
]

export function ModelFiltersBar({ filters, onChange }: ModelFiltersBarProps) {
  const hasActiveFilters = !!(filters.search || filters.stage || filters.engine_id || filters.status)

  return (
    <div className="flex flex-wrap gap-3 items-center">
      {/* Search Input */}
      <div className="relative flex-1 min-w-[200px] max-w-sm">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <input
          type="text"
          placeholder={S.modelFilters.searchPlaceholder}
          className="w-full h-10 pl-9 pr-3 rounded-md border border-input bg-background text-sm"
          value={filters.search || ''}
          onChange={(e) => onChange({ ...filters, search: e.target.value || undefined })}
        />
      </div>

      {/* Stage Filter */}
      <Select
        value={filters.stage || ''}
        onValueChange={(v) => onChange({ ...filters, stage: v || undefined })}
      >
        <SelectTrigger className="w-[140px]">
          <SelectValue placeholder={S.modelFilters.allStages} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="">{S.modelFilters.allStages}</SelectItem>
          {STAGES.map((s) => (
            <SelectItem key={s.value} value={s.value}>
              {s.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {/* Runtime Filter */}
      <Select
        value={filters.engine_id || ''}
        onValueChange={(v) => onChange({ ...filters, engine_id: v || undefined })}
      >
        <SelectTrigger className="w-[160px]">
          <SelectValue placeholder={S.modelFilters.allRuntimes} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="">{S.modelFilters.allRuntimes}</SelectItem>
          {RUNTIMES.map((r) => (
            <SelectItem key={r.value} value={r.value}>
              {r.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {/* Status Filter */}
      <Select
        value={filters.status || ''}
        onValueChange={(v) => onChange({ ...filters, status: (v as ModelStatus) || undefined })}
      >
        <SelectTrigger className="w-[150px]">
          <SelectValue placeholder={S.modelFilters.allStatuses} />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="">{S.modelFilters.allStatuses}</SelectItem>
          {STATUSES.map((s) => (
            <SelectItem key={s.value} value={s.value}>
              {s.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      {/* Clear Filters */}
      {hasActiveFilters && (
        <Button variant="ghost" size="sm" onClick={() => onChange({})}>
          <X className="h-4 w-4 mr-1" />
          Clear
        </Button>
      )}
    </div>
  )
}
