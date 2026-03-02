import { useState, useMemo } from 'react'
import {
  RefreshCw,
  CheckCircle,
  Cloud,
  AlertCircle,
  Loader2,
  Package,
} from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import {
  useModelRegistry,
  usePullModel,
  useRemoveModel,
  useResolveHFModel,
  useSyncModels,
} from '@/hooks/useModelRegistry'
import { ModelCard } from '@/components/ModelCard'
import { ModelFiltersBar } from '@/components/ModelFiltersBar'
import { HFModelInput } from '@/components/HFModelInput'
import type { ModelFilters } from '@/api/types'

export function Models() {
  const [filters, setFilters] = useState<ModelFilters>({})
  const { data, isLoading, error } = useModelRegistry(filters)
  const pullModel = usePullModel()
  const removeModel = useRemoveModel()
  const resolveHF = useResolveHFModel()
  const syncModels = useSyncModels()

  const models = data?.data ?? []

  // Group models by status for display
  const { readyModels, downloadingModels, availableModels, failedModels } = useMemo(() => {
    return {
      readyModels: models.filter((m) => m.status === 'ready'),
      downloadingModels: models.filter((m) => m.status === 'downloading'),
      availableModels: models.filter((m) => m.status === 'not_downloaded'),
      failedModels: models.filter((m) => m.status === 'failed'),
    }
  }, [models])

  // Client-side search filtering (backend handles stage/runtime/status)
  const filteredModels = useMemo(() => {
    if (!filters.search) {
      return { readyModels, downloadingModels, availableModels, failedModels }
    }
    const search = filters.search.toLowerCase()
    const filterFn = (m: (typeof models)[0]) =>
      m.id.toLowerCase().includes(search) ||
      m.name?.toLowerCase().includes(search) ||
      m.runtime.toLowerCase().includes(search)

    return {
      readyModels: readyModels.filter(filterFn),
      downloadingModels: downloadingModels.filter(filterFn),
      availableModels: availableModels.filter(filterFn),
      failedModels: failedModels.filter(filterFn),
    }
  }, [readyModels, downloadingModels, availableModels, failedModels, filters.search])

  const handleResolve = (modelId: string) => {
    resolveHF.mutate({ model_id: modelId, auto_register: true })
  }

  if (error) {
    return (
      <div className="p-6">
        <div className="text-center py-12">
          <AlertCircle className="h-12 w-12 mx-auto mb-4 text-red-500" />
          <p className="text-red-500">Failed to load model registry</p>
          <p className="text-sm text-muted-foreground mt-1">{error.message}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6 p-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Models</h1>
          <p className="text-muted-foreground">
            Manage transcription models and download from HuggingFace
          </p>
        </div>
        <Button
          variant="outline"
          onClick={() => syncModels.mutate()}
          disabled={syncModels.isPending}
        >
          <RefreshCw className={cn('h-4 w-4 mr-2', syncModels.isPending && 'animate-spin')} />
          Sync with Disk
        </Button>
      </div>

      {/* Filters */}
      <ModelFiltersBar filters={filters} onChange={setFilters} />

      {/* Add from HuggingFace */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Add Model from HuggingFace</CardTitle>
        </CardHeader>
        <CardContent>
          <HFModelInput
            onResolve={handleResolve}
            isLoading={resolveHF.isPending}
            result={resolveHF.data}
            error={resolveHF.error}
          />
        </CardContent>
      </Card>

      {/* Loading State */}
      {isLoading && (
        <div className="text-center py-12">
          <Loader2 className="h-8 w-8 animate-spin mx-auto mb-4 text-muted-foreground" />
          <p className="text-muted-foreground">Loading models...</p>
        </div>
      )}

      {/* Empty State */}
      {!isLoading && models.length === 0 && (
        <div className="text-center py-12">
          <Package className="h-12 w-12 mx-auto mb-4 opacity-50" />
          <p className="text-muted-foreground">No models found</p>
          <p className="text-sm text-muted-foreground mt-1">
            Add a model from HuggingFace or sync with disk
          </p>
        </div>
      )}

      {/* Downloading Models */}
      {filteredModels.downloadingModels.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-3 flex items-center gap-2">
            <Loader2 className="h-4 w-4 animate-spin" />
            Downloading ({filteredModels.downloadingModels.length})
          </h2>
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {filteredModels.downloadingModels.map((model) => (
              <ModelCard key={model.id} model={model} />
            ))}
          </div>
        </section>
      )}

      {/* Ready Models */}
      {filteredModels.readyModels.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-3 flex items-center gap-2">
            <CheckCircle className="h-4 w-4 text-green-500" />
            Ready ({filteredModels.readyModels.length})
          </h2>
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {filteredModels.readyModels.map((model) => (
              <ModelCard
                key={model.id}
                model={model}
                onRemove={() => removeModel.mutate(model.id)}
                isRemoving={removeModel.isPending && removeModel.variables === model.id}
              />
            ))}
          </div>
        </section>
      )}

      {/* Available Models */}
      {filteredModels.availableModels.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-3 flex items-center gap-2">
            <Cloud className="h-4 w-4 text-muted-foreground" />
            Available to Download ({filteredModels.availableModels.length})
          </h2>
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {filteredModels.availableModels.map((model) => (
              <ModelCard
                key={model.id}
                model={model}
                onPull={() => pullModel.mutate({ modelId: model.id })}
                isPulling={pullModel.isPending && pullModel.variables?.modelId === model.id}
              />
            ))}
          </div>
        </section>
      )}

      {/* Failed Models */}
      {filteredModels.failedModels.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-3 flex items-center gap-2">
            <AlertCircle className="h-4 w-4 text-red-500" />
            Failed ({filteredModels.failedModels.length})
          </h2>
          <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
            {filteredModels.failedModels.map((model) => (
              <ModelCard
                key={model.id}
                model={model}
                onPull={() => pullModel.mutate({ modelId: model.id, force: true })}
                isPulling={pullModel.isPending && pullModel.variables?.modelId === model.id}
              />
            ))}
          </div>
        </section>
      )}
    </div>
  )
}

export default Models
