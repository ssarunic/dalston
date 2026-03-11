import { Clock, Download, ExternalLink, Globe, Heart, Loader2 } from 'lucide-react'
import { Card, CardContent, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
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

interface ModelCardProps {
  model: ModelRegistryEntry
  onPull?: () => void
  onRemove?: () => void
  isPulling?: boolean
  isRemoving?: boolean
}

export function ModelCard({ model, onPull, onRemove, isPulling, isRemoving }: ModelCardProps) {
  const statusColors: Record<string, string> = {
    ready: 'bg-green-500',
    downloading: 'bg-yellow-500 animate-pulse',
    not_downloaded: 'bg-gray-400',
    failed: 'bg-red-500',
  }

  const statusLabels: Record<string, string> = {
    ready: 'Ready',
    downloading: 'Downloading',
    not_downloaded: 'Not Downloaded',
    failed: 'Failed',
  }

  return (
    <Card className="flex flex-col">
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-2">
          <div className="flex-1 min-w-0">
            <CardTitle className="text-base truncate" title={model.id}>
              {model.id}
            </CardTitle>
            {model.name && (
              <p className="text-sm text-muted-foreground truncate" title={model.name}>
                {model.name}
              </p>
            )}
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            <div
              className={cn('w-2 h-2 rounded-full', statusColors[model.status])}
              title={statusLabels[model.status]}
            />
            {model.size_bytes && (
              <span className="text-xs text-muted-foreground">{formatBytes(model.size_bytes)}</span>
            )}
          </div>
        </div>
      </CardHeader>

      <CardContent className="flex-1 space-y-3">
        {/* Runtime & Stage */}
        <div className="flex flex-wrap gap-1.5">
          <Badge variant="secondary">{model.engine_id}</Badge>
          <Badge variant="outline">{model.stage}</Badge>
        </div>

        {/* Capabilities */}
        <div className="flex flex-wrap gap-1.5">
          {model.word_timestamps && (
            <Badge variant="outline" className="text-xs">
              <Clock className="h-3 w-3 mr-1" />
              timestamps
            </Badge>
          )}
          {model.punctuation && (
            <Badge variant="outline" className="text-xs">
              punctuation
            </Badge>
          )}
          {model.streaming && (
            <Badge variant="outline" className="text-xs">
              streaming
            </Badge>
          )}
          {model.supports_cpu && (
            <Badge variant="outline" className="text-xs">
              CPU
            </Badge>
          )}
        </div>

        {/* Languages */}
        {model.languages && model.languages.length > 0 && (
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Globe className="h-3 w-3 flex-shrink-0" />
            <span className="truncate">
              {model.languages.length > 5
                ? `${model.languages.slice(0, 5).join(', ')} +${model.languages.length - 5}`
                : model.languages.join(', ')}
            </span>
          </div>
        )}

        {/* HF Stats */}
        {(model.metadata?.downloads || model.metadata?.likes) && (
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
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

        {/* Hardware Requirements */}
        {(model.min_vram_gb || model.min_ram_gb) && (
          <div className="text-xs text-muted-foreground">
            {model.min_vram_gb && <span>VRAM: {model.min_vram_gb}GB</span>}
            {model.min_vram_gb && model.min_ram_gb && <span className="mx-1">•</span>}
            {model.min_ram_gb && <span>RAM: {model.min_ram_gb}GB</span>}
          </div>
        )}

        {/* Download Progress */}
        {model.status === 'downloading' && model.download_progress !== undefined && (
          <div className="space-y-1">
            <div className="h-1.5 w-full bg-secondary rounded-full overflow-hidden">
              <div
                className="h-full bg-primary transition-all duration-300"
                style={{ width: `${model.download_progress}%` }}
              />
            </div>
            <p className="text-xs text-muted-foreground text-right">{model.download_progress}%</p>
          </div>
        )}

        {/* Error Message */}
        {model.status === 'failed' && model.metadata?.error && (
          <p className="text-xs text-red-500 truncate" title={model.metadata.error}>
            {model.metadata.error}
          </p>
        )}
      </CardContent>

      <CardFooter className="pt-2">
        <div className="flex items-center justify-between w-full">
          {model.source === 'huggingface' && model.loaded_model_id && (
            <a
              href={`https://huggingface.co/${model.loaded_model_id}`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1"
            >
              <ExternalLink className="h-3 w-3" />
              View on HF
            </a>
          )}
          <div className="flex gap-2 ml-auto">
            {model.status === 'ready' && onRemove && (
              <Button variant="outline" size="sm" onClick={onRemove} disabled={isRemoving}>
                {isRemoving ? <Loader2 className="h-3 w-3 animate-spin" /> : 'Remove'}
              </Button>
            )}
            {(model.status === 'not_downloaded' || model.status === 'failed') && onPull && (
              <Button size="sm" onClick={onPull} disabled={isPulling}>
                {isPulling ? (
                  <Loader2 className="h-3 w-3 animate-spin mr-1" />
                ) : (
                  <Download className="h-3 w-3 mr-1" />
                )}
                Pull
              </Button>
            )}
          </div>
        </div>
      </CardFooter>
    </Card>
  )
}
