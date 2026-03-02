import { useState, useMemo } from 'react'
import {
  RefreshCw,
  CheckCircle,
  Cloud,
  AlertCircle,
  Loader2,
  Package,
  Plus,
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
import { ModelTable } from '@/components/ModelTable'
import { ModelFiltersBar } from '@/components/ModelFiltersBar'
import { AddModelDialog } from '@/components/AddModelDialog'
import type { ModelFilters } from '@/api/types'

export function Models() {
  const [filters, setFilters] = useState<ModelFilters>({})
  const [addDialogOpen, setAddDialogOpen] = useState(false)
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

  const handlePull = (modelId: string, force = false) => {
    pullModel.mutate({ modelId, force })
  }

  const handleRemove = (modelId: string) => {
    removeModel.mutate(modelId)
  }

  if (error) {
    return (
      <div>
        <div className="text-center py-12">
          <AlertCircle className="h-12 w-12 mx-auto mb-4 text-red-500" />
          <p className="text-red-500">Failed to load model registry</p>
          <p className="text-sm text-muted-foreground mt-1">{error.message}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Models</h1>
          <p className="text-muted-foreground">
            Manage transcription models and download from HuggingFace
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            onClick={() => syncModels.mutate()}
            disabled={syncModels.isPending}
          >
            <RefreshCw className={cn('h-4 w-4 mr-2', syncModels.isPending && 'animate-spin')} />
            Sync with Disk
          </Button>
          <Button onClick={() => setAddDialogOpen(true)}>
            <Plus className="h-4 w-4 mr-2" />
            Add from HF
          </Button>
        </div>
      </div>

      {/* Filters */}
      <ModelFiltersBar filters={filters} onChange={setFilters} />

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
        <Card>
          <CardHeader className="py-3">
            <CardTitle className="text-base flex items-center gap-2">
              <Loader2 className="h-4 w-4 animate-spin" />
              Downloading ({filteredModels.downloadingModels.length})
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            <ModelTable models={filteredModels.downloadingModels} />
          </CardContent>
        </Card>
      )}

      {/* Ready Models */}
      {filteredModels.readyModels.length > 0 && (
        <Card>
          <CardHeader className="py-3">
            <CardTitle className="text-base flex items-center gap-2">
              <CheckCircle className="h-4 w-4 text-green-500" />
              Ready ({filteredModels.readyModels.length})
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            <ModelTable
              models={filteredModels.readyModels}
              onRemove={handleRemove}
              removingId={removeModel.isPending ? removeModel.variables : undefined}
            />
          </CardContent>
        </Card>
      )}

      {/* Available Models */}
      {filteredModels.availableModels.length > 0 && (
        <Card>
          <CardHeader className="py-3">
            <CardTitle className="text-base flex items-center gap-2">
              <Cloud className="h-4 w-4 text-muted-foreground" />
              Available to Download ({filteredModels.availableModels.length})
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            <ModelTable
              models={filteredModels.availableModels}
              onPull={(modelId) => handlePull(modelId)}
              pullingId={pullModel.isPending ? pullModel.variables?.modelId : undefined}
            />
          </CardContent>
        </Card>
      )}

      {/* Failed Models */}
      {filteredModels.failedModels.length > 0 && (
        <Card>
          <CardHeader className="py-3">
            <CardTitle className="text-base flex items-center gap-2">
              <AlertCircle className="h-4 w-4 text-red-500" />
              Failed ({filteredModels.failedModels.length})
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            <ModelTable
              models={filteredModels.failedModels}
              onPull={(modelId) => handlePull(modelId, true)}
              pullingId={pullModel.isPending ? pullModel.variables?.modelId : undefined}
            />
          </CardContent>
        </Card>
      )}

      {/* Add Model Dialog */}
      <AddModelDialog
        open={addDialogOpen}
        onOpenChange={setAddDialogOpen}
        onResolve={handleResolve}
        isLoading={resolveHF.isPending}
        result={resolveHF.data}
        error={resolveHF.error}
      />
    </div>
  )
}

export default Models
