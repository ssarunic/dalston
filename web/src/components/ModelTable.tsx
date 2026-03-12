import { useState } from 'react'
import {
  ChevronDown,
  ChevronRight,
  Clock,
  CloudDownload,
  Download,
  ExternalLink,
  Globe,
  Heart,
  Loader2,
  Trash2,
  X,
  Zap,
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
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog } from '@/components/ui/dialog'
import { cn } from '@/lib/utils'
import { formatBytes, formatDownloadProgress, formatNumber } from '@/lib/format'
import { S } from '@/lib/strings'
import type { ModelRegistryEntry } from '@/api/types'

const statusColors: Record<string, string> = {
  ready: 'bg-green-500',
  downloading: 'bg-yellow-500 animate-pulse',
  not_downloaded: 'bg-gray-400',
  failed: 'bg-red-500',
}

const statusLabels: Record<string, string> = {
  ready: S.modelTable.statusReady,
  downloading: S.modelTable.statusDownloading,
  not_downloaded: S.modelTable.statusAvailable,
  failed: S.modelTable.statusFailed,
}

interface ModelTableProps {
  models: ModelRegistryEntry[]
  onPull?: (modelId: string) => void
  onRemove?: (modelId: string) => void
  onPurge?: (modelId: string) => void
  pullingId?: string
  removingId?: string
  purgingId?: string
}

export function ModelTable({
  models,
  onPull,
  onRemove,
  onPurge,
  pullingId,
  removingId,
  purgingId,
}: ModelTableProps) {
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set())
  const [purgeConfirm, setPurgeConfirm] = useState<ModelRegistryEntry | null>(null)

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

  const handlePurgeConfirm = () => {
    if (purgeConfirm && onPurge) {
      onPurge(purgeConfirm.id)
      setPurgeConfirm(null)
    }
  }

  return (
    <>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-8"></TableHead>
            <TableHead>Model</TableHead>
            <TableHead>{S.modelTable.colRuntime}</TableHead>
            <TableHead>{S.common.colStatus}</TableHead>
            <TableHead>{S.modelTable.colSize}</TableHead>
            <TableHead>{S.modelTable.colCapabilities}</TableHead>
            <TableHead className="text-right">{S.common.colActions}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {models.map((model) => {
            const isExpanded = expandedIds.has(model.id)
            const isPulling = pullingId === model.id
            const isRemoving = removingId === model.id
            const isPurging = purgingId === model.id

            return (
              <ModelTableRow
                key={model.id}
                model={model}
                isExpanded={isExpanded}
                onToggle={() => toggleExpanded(model.id)}
                onPull={onPull}
                onRemove={onRemove}
                onPurgeClick={onPurge ? () => setPurgeConfirm(model) : undefined}
                isPulling={isPulling}
                isRemoving={isRemoving}
                isPurging={isPurging}
              />
            )
          })}
        </TableBody>
      </Table>

      {/* Purge Confirmation Dialog */}
      <Dialog
        open={purgeConfirm !== null}
        onOpenChange={(open) => {
          if (!open) setPurgeConfirm(null)
        }}
      >
        <Card className="w-full max-w-md mx-4">
          <CardHeader>
            <CardTitle className="text-destructive">{S.modelTable.deleteTitle}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-muted-foreground">
              {S.modelTable.deleteConfirm}
            </p>
            {purgeConfirm && (
              <div className="bg-muted p-3 rounded-md">
                <p className="font-mono text-sm truncate">{purgeConfirm.id}</p>
                {purgeConfirm.name && purgeConfirm.name !== purgeConfirm.id && (
                  <p className="text-sm text-muted-foreground">{purgeConfirm.name}</p>
                )}
              </div>
            )}
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setPurgeConfirm(null)}>
                Cancel
              </Button>
              <Button variant="destructive" onClick={handlePurgeConfirm}>
                Delete
              </Button>
            </div>
          </CardContent>
        </Card>
      </Dialog>
    </>
  )
}

interface ModelTableRowProps {
  model: ModelRegistryEntry
  isExpanded: boolean
  onToggle: () => void
  onPull?: (modelId: string) => void
  onRemove?: (modelId: string) => void
  onPurgeClick?: () => void
  isPulling: boolean
  isRemoving: boolean
  isPurging: boolean
}

function ModelTableRow({
  model,
  isExpanded,
  onToggle,
  onPull,
  onRemove,
  onPurgeClick,
  isPulling,
  isRemoving,
  isPurging,
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
          <Badge variant="secondary">{model.engine_id}</Badge>
        </TableCell>
        <TableCell>
          <div className="flex flex-col gap-1">
            <div className="flex items-center gap-2">
              <div
                className={cn('w-2 h-2 rounded-full flex-shrink-0', statusColors[model.status])}
              />
              <span className="text-sm">{statusLabels[model.status]}</span>
              {model.status === 'downloading' && typeof model.download_progress === 'number' && (
                <span className="text-xs text-muted-foreground">{model.download_progress}%</span>
              )}
            </div>
            {model.status === 'downloading' && (
              <div className="flex items-center gap-2">
                <div className="h-1.5 w-24 bg-secondary rounded-full overflow-hidden flex-shrink-0">
                  <div
                    className="h-full bg-blue-500 rounded-full transition-all duration-500"
                    style={{ width: `${model.download_progress ?? 0}%` }}
                  />
                </div>
                {formatDownloadProgress(model) && (
                  <span className="text-[11px] text-muted-foreground whitespace-nowrap">
                    {formatDownloadProgress(model)}
                  </span>
                )}
              </div>
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
                {S.modelTable.capWord}
              </Badge>
            )}
            {!model.word_timestamps && (
              <Badge variant="outline" className="text-xs text-muted-foreground">
                <Clock className="h-3 w-3 mr-1" />
                {S.modelTable.capSegment}
              </Badge>
            )}
            {model.streaming && (
              <Badge variant="outline" className="text-xs">
                {S.modelTable.capStream}
              </Badge>
            )}
            {!model.supports_cpu && (
              <Badge variant="outline" className="text-xs text-amber-600 border-amber-400">
                <Zap className="h-3 w-3 mr-1" />
                {S.modelTable.capGpuOnly}
              </Badge>
            )}
          </div>
        </TableCell>
        <TableCell className="text-right">
          <div className="flex items-center justify-end gap-1" onClick={(e) => e.stopPropagation()}>
            {/* Ready models: remove files button */}
            {model.status === 'ready' && onRemove && (
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8"
                onClick={() => onRemove(model.id)}
                disabled={isRemoving || isPurging}
                title={S.modelTable.removeDownloaded}
              >
                {isRemoving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Trash2 className="h-4 w-4" />}
              </Button>
            )}
            {/* Downloading: show spinner */}
            {model.status === 'downloading' && (
              <Button variant="ghost" size="icon" className="h-8 w-8" disabled title={S.modelTable.downloading}>
                <Loader2 className="h-4 w-4 animate-spin" />
              </Button>
            )}
            {/* Not downloaded/failed: download button */}
            {(model.status === 'not_downloaded' || model.status === 'failed') && onPull && (
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8"
                onClick={() => onPull(model.id)}
                disabled={isPulling || isPurging}
                title={S.modelTable.downloadModel}
              >
                {isPulling ? <Loader2 className="h-4 w-4 animate-spin" /> : <CloudDownload className="h-4 w-4" />}
              </Button>
            )}
            {/* Delete from registry (all non-downloading states) */}
            {model.status !== 'downloading' && onPurgeClick && (
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8 text-destructive hover:text-destructive"
                onClick={onPurgeClick}
                disabled={isPurging || isRemoving}
                title={S.modelTable.deleteFromRegistry}
              >
                {isPurging ? <Loader2 className="h-4 w-4 animate-spin" /> : <X className="h-4 w-4" />}
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
        <h4 className="text-sm font-medium">{S.modelTable.colCapabilities}</h4>
        <div className="space-y-1 text-sm text-muted-foreground">
          <div className="flex items-center gap-2">
            <span className={model.word_timestamps ? 'text-green-500' : ''}>
              {model.word_timestamps ? S.modelTable.wordTimestamps : S.modelTable.segmentTimestamps}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className={model.punctuation ? 'text-green-500' : ''}>
              {model.punctuation ? S.modelTable.punctuation : S.modelTable.noPunctuation}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span className={model.streaming ? 'text-green-500' : ''}>
              {model.streaming ? S.modelTable.streamingSupport : S.modelTable.batchOnly}
            </span>
          </div>
        </div>
      </div>

      {/* Hardware */}
      <div className="space-y-2">
        <h4 className="text-sm font-medium">{S.modelTable.hardwareTitle}</h4>
        <div className="space-y-1 text-sm text-muted-foreground">
          {model.min_vram_gb && <div>VRAM: {model.min_vram_gb} GB</div>}
          {model.min_ram_gb && <div>RAM: {model.min_ram_gb} GB</div>}
          <div className="flex items-center gap-2">
            <span className={model.supports_cpu ? 'text-green-500' : ''}>
              {model.supports_cpu ? S.modelTable.cpuCompatible : S.modelTable.gpuRequired}
            </span>
          </div>
        </div>
      </div>

      {/* Languages & Metadata */}
      <div className="space-y-2">
        <h4 className="text-sm font-medium">{S.modelTable.languagesAndInfo}</h4>
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
              <span>{S.modelTable.multilingual}</span>
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

          {/* HF Link - source contains HF repo ID like "Systran/faster-whisper-base" */}
          {model.source?.includes('/') && (
            <a
              href={`https://huggingface.co/${model.source}`}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-primary hover:underline pt-1"
            >
              <ExternalLink className="h-3 w-3" />
              {S.modelTable.viewOnHuggingFace}
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
