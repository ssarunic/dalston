import { AlertTriangle, Download, Loader2, XCircle } from 'lucide-react'
import { useModelRegistryEntry } from '@/hooks/useModelRegistry'

interface ModelCompatibilityWarningProps {
  modelId: string
  language?: string
}

/**
 * Shows compatibility warnings for a selected model:
 * - Not downloaded (will be downloaded on job start)
 * - Currently downloading
 * - Failed to download
 * - Language incompatibility
 */
export function ModelCompatibilityWarning({
  modelId,
  language,
}: ModelCompatibilityWarningProps) {
  const { data: model, isLoading } = useModelRegistryEntry(modelId)

  // Don't show anything while loading or if model not found (custom HF model)
  if (isLoading) return null

  const warnings: { type: 'warning' | 'error' | 'info'; message: string; icon: React.ReactNode }[] = []

  if (model) {
    // Check download status
    if (model.status === 'not_downloaded') {
      warnings.push({
        type: 'info',
        message: 'This model is not downloaded. It will be downloaded when the job starts.',
        icon: <Download className="h-3.5 w-3.5 shrink-0" />,
      })
    } else if (model.status === 'downloading') {
      const progress = model.download_progress
      warnings.push({
        type: 'info',
        message: progress
          ? `This model is downloading (${progress}%). Job will wait for completion.`
          : 'This model is currently downloading. Job will wait for completion.',
        icon: <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />,
      })
    } else if (model.status === 'failed') {
      const error = model.metadata?.error
      warnings.push({
        type: 'error',
        message: error
          ? `This model failed to download: ${error}`
          : 'This model failed to download. The job may fail.',
        icon: <XCircle className="h-3.5 w-3.5 shrink-0" />,
      })
    }

    // Check language compatibility
    if (
      language &&
      language !== 'auto' &&
      model.languages &&
      model.languages.length > 0 &&
      !model.languages.includes(language)
    ) {
      const langList =
        model.languages.length > 5
          ? `${model.languages.slice(0, 5).join(', ')}, +${model.languages.length - 5} more`
          : model.languages.join(', ')
      warnings.push({
        type: 'warning',
        message: `This model may not support "${language}". Supported: ${langList}`,
        icon: <AlertTriangle className="h-3.5 w-3.5 shrink-0" />,
      })
    }
  } else {
    // Custom model not in registry - show info
    warnings.push({
      type: 'info',
      message:
        'This is a custom model. It will be resolved and downloaded when the job starts.',
      icon: <Download className="h-3.5 w-3.5 shrink-0" />,
    })
  }

  if (warnings.length === 0) return null

  return (
    <div className="space-y-1.5 mt-2">
      {warnings.map((warning, i) => (
        <p
          key={i}
          className={`text-sm flex items-start gap-1.5 ${
            warning.type === 'error'
              ? 'text-red-600 dark:text-red-400'
              : warning.type === 'warning'
                ? 'text-yellow-600 dark:text-yellow-400'
                : 'text-muted-foreground'
          }`}
        >
          {warning.icon}
          <span>{warning.message}</span>
        </p>
      ))}
    </div>
  )
}
