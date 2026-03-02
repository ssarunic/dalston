import { useState } from 'react'
import {
  ChevronDown,
  ChevronRight,
  Clock,
  Download,
  ExternalLink,
  Globe,
  Heart,
  Loader2,
  Cpu,
} from 'lucide-react'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import type { ModelRegistryEntry } from '@/api/types'

// Format bytes to human-readable string
function formatBytes(bytes: number | null): string {
  if (bytes === null || bytes === 0) return '-'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(1024))
  return `${(bytes / Math.pow(1024, i)).toFixed(i > 0 ? 1 : 0)} ${units[i]}`
}

// Format large numbers with K/M suffix
function formatNumber(num: number | undefined): string {
  if (num === undefined) return '-'
  if (num >= 1_000_000) return `${(num / 1_000_000).toFixed(1)}M`
  if (num >= 1_000) return `${(num / 1_000).toFixed(1)}K`
  return num.toString()
}

const statusColors: Record<string, string> = {
  ready: 'bg-green-500',
  downloading: 'bg-yellow-500 animate-pulse',
  not_downloaded: 'bg-gray-400',
  failed: 'bg-red-500',
}

const statusLabels: Record<string, string> = {
  ready: 'Ready',
  downloading: 'Downloading',
  not_downloaded: 'Available',
  failed: 'Failed',
}

interface ModelTableProps {
  models: ModelRegistryEntry[]
  onPull?: (modelId: string) => void
  onRemove?: (modelId: string) => void
  pullingId?: string
  removingId?: string
}

export function ModelTable({
  models,
  onPull,
  onRemove,
  pullingId,
  removingId,
}: ModelTableProps) {
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set())

  const toggleExpanded = (id: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }

  if (models.length === 0) {
    return null
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-8"></TableHead>
          <TableHead>Model</TableHead>
          <TableHead>Runtime</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Size</TableHead>
          <TableHead>Capabilities</TableHead>
          <TableHead className="text-right">Actions</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {models.map((model) => {
          const isExpanded = expandedIds.has(model.id)
          const isPulling = pullingId === model.id
          const isRemoving = removingId === model.id

          return (
            <ModelTableRow
              key={model.id}
              model={model}
              isExpanded={isExpanded}
              onToggle={() => toggleExpanded(model.id)}
              onPull={onPull}
              onRemove={onRemove}
              isPulling={isPulling}
              isRemoving={isRemoving}
            />
          )
        })}
      </TableBody>
    </Table>
  )
}

interface ModelTableRowProps {
  model: ModelRegistryEntry
  isExpanded: boolean
  onToggle: () => void
  onPull?: (modelId: string) => void
  onRemove?: (modelId: string) => void
  isPulling: boolean
  isRemoving: boolean
}

function ModelTableRow({
  model,
  isExpanded,
  onToggle,
  onPull,
  onRemove,
  isPulling,
  isRemoving,
}: ModelTableRowProps) {
  return (
    <>
      <TableRow
        className="cursor-pointer"
        onClick={onToggle}
      >
        <TableCell className="w-8 pr-0">
          <button
            className="p-1 hover:bg-accent rounded"
            onClick={(e) => {
              e.stopPropagation()
              onToggle()
            }}
          >
            {isExpanded ? (
              <ChevronDown className="h-4 w-4 text-muted-foreground" />
            ) : (
              <ChevronRight className="h-4 w-4 text-muted-foreground" />
            )}
          </button>
        </TableCell>
        <TableCell>
          <div className="min-w-0">
            <div className="font-medium truncate max-w-[200px]" title={model.id}>
              {model.id}
            </div>
            {model.name && model.name !== model.id && (
              <div className="text-xs text-muted-foreground truncate max-w-[200px]" title={model.name}>
                {model.name}
              </div>
            )}
          </div>
        </TableCell>
        <TableCell>
          <Badge variant="secondary">{model.runtime}</Badge>
        </TableCell>
        <TableCell>
          <div className="flex items-center gap-2">
            <div
              className={cn('w-2 h-2 rounded-full flex-shrink-0', statusColors[model.status])}
            />
            <span className="text-sm">{statusLabels[model.status]}</span>
            {model.status === 'downloading' && model.download_progress !== undefined && (
              <span className="text-xs text-muted-foreground">({model.download_progress}%)</span>
            )}
          </div>
        </TableCell>
        <TableCell className="text-muted-foreground">
          {formatBytes(model.size_bytes)}
        </TableCell>
        <TableCell>
          <div className="flex flex-wrap gap-1">
            {model.word_timestamps && (
              <Badge variant="outline" className="text-xs">
                <Clock className="h-3 w-3 mr-1" />
                word
              </Badge>
            )}
            {!model.word_timestamps && (
              <Badge variant="outline" className="text-xs text-muted-foreground">
                <Clock className="h-3 w-3 mr-1" />
                segment
              </Badge>
            )}
            {model.streaming && (
              <Badge variant="outline" className="text-xs">
                stream
              </Badge>
            )}
            {model.supports_cpu && (
              <Badge variant="outline" className="text-xs">
                <Cpu className="h-3 w-3 mr-1" />
                CPU
              </Badge>
            )}
          </div>
        </TableCell>
        <TableCell className="text-right">
          <div className="flex items-center justify-end gap-2" onClick={(e) => e.stopPropagation()}>
            {model.status === 'ready' && onRemove && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => onRemove(model.id)}
                disabled={isRemoving}
              >
                {isRemoving ? <Loader2 className="h-3 w-3 animate-spin" /> : 'Remove'}
              </Button>
            )}
            {model.status === 'downloading' && (
              <Button size="sm" disabled variant="outline">
                <Loader2 className="h-3 w-3 animate-spin mr-1" />
                Downloading
              </Button>
            )}
            {(model.status === 'not_downloaded' || model.status === 'failed') && onPull && (
              <Button
                size="sm"
                onClick={() => onPull(model.id)}
                disabled={isPulling}
              >
                {isPulling ? (
                  <Loader2 className="h-3 w-3 animate-spin mr-1" />
                ) : (
                  <Download className="h-3 w-3 mr-1" />
                )}
                Pull
              </Button>
            )}
          </div>
        </TableCell>
      </TableRow>

      {/* Expanded details row */}
      {isExpanded && (
        <TableRow className="bg-muted/30 hover:bg-muted/30">
          <TableCell colSpan={7} className="py-4">
            <ModelExpandedDetails model={model} />
          </TableCell>
        </TableRow>
      )}
    </>
  )
}

function ModelExpandedDetails({ model }: { model: ModelRegistryEntry }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-6 px-2">
      {/* Capabilities */}
      <div className="space-y-2">
        <h4 className="text-sm font-medium">Capabilities</h4>
        <div className="space-y-1 text-sm text-muted-foreground">
          <div className="flex items-center gap-2">
            <span className={model.word_timestamps ? 'text-green-500' : ''}>
              {model.word_timestamps ? 'Word-level timestamps' : 'Segment-level timestamps only'}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className={model.punctuation ? 'text-green-500' : ''}>
              {model.punctuation ? 'Punctuation' : 'No punctuation'}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className={model.streaming ? 'text-green-500' : ''}>
              {model.streaming ? 'Streaming support' : 'Batch only'}
            </span>
          </div>
        </div>
      </div>

      {/* Hardware */}
      <div className="space-y-2">
        <h4 className="text-sm font-medium">Hardware Requirements</h4>
        <div className="space-y-1 text-sm text-muted-foreground">
          {model.min_vram_gb && <div>VRAM: {model.min_vram_gb} GB</div>}
          {model.min_ram_gb && <div>RAM: {model.min_ram_gb} GB</div>}
          <div className="flex items-center gap-2">
            <span className={model.supports_cpu ? 'text-green-500' : ''}>
              {model.supports_cpu ? 'CPU compatible' : 'GPU required'}
            </span>
          </div>
        </div>
      </div>

      {/* Languages & Metadata */}
      <div className="space-y-2">
        <h4 className="text-sm font-medium">Languages & Info</h4>
        <div className="space-y-1 text-sm text-muted-foreground">
          {model.languages && model.languages.length > 0 ? (
            <div className="flex items-start gap-2">
              <Globe className="h-4 w-4 flex-shrink-0 mt-0.5" />
              <span className="break-words">
                {model.languages.length > 10
                  ? `${model.languages.slice(0, 10).join(', ')} +${model.languages.length - 10} more`
                  : model.languages.join(', ')}
              </span>
            </div>
          ) : (
            <div className="flex items-center gap-2">
              <Globe className="h-4 w-4" />
              <span>Multilingual</span>
            </div>
          )}

          {/* HF Stats */}
          {(model.metadata?.downloads || model.metadata?.likes) && (
            <div className="flex items-center gap-4 pt-1">
              {model.metadata.downloads && (
                <span className="flex items-center gap-1">
                  <Download className="h-3 w-3" />
                  {formatNumber(model.metadata.downloads)}
                </span>
              )}
              {model.metadata.likes && (
                <span className="flex items-center gap-1">
                  <Heart className="h-3 w-3" />
                  {formatNumber(model.metadata.likes)}
                </span>
              )}
            </div>
          )}

          {/* HF Link */}
          {model.source === 'huggingface' && model.runtime_model_id && (
            <a
              href={`https://huggingface.co/${model.runtime_model_id}`}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-primary hover:underline pt-1"
            >
              <ExternalLink className="h-3 w-3" />
              View on HuggingFace
            </a>
          )}
        </div>

        {/* Error message for failed models */}
        {model.status === 'failed' && model.metadata?.error && (
          <div className="pt-2">
            <p className="text-xs text-red-500">{model.metadata.error}</p>
          </div>
        )}
      </div>
    </div>
  )
}
