import { useState, useMemo } from 'react'
import {
  RefreshCw,
  AlertCircle,
  Loader2,
  Package,
  Plus,
} from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
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

  // Client-side search filtering
  const filteredModels = useMemo(() => {
    if (!filters.search) return models
    const search = filters.search.toLowerCase()
    return models.filter(
      (m) =>
        m.id.toLowerCase().includes(search) ||
        m.name?.toLowerCase().includes(search) ||
        m.runtime.toLowerCase().includes(search)
    )
  }, [models, filters.search])

  const handleResolve = (modelId: string) => {
    resolveHF.mutate({ model_id: modelId, auto_register: true })
  }

  const handlePull = (modelId: string) => {
    // Use force=true for failed models to retry
    const model = models.find((m) => m.id === modelId)
    const force = model?.status === 'failed'
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

      {/* All Models in One Table */}
      {!isLoading && filteredModels.length > 0 && (
        <Card>
          <CardContent className="pt-6">
            <ModelTable
              models={filteredModels}
              onPull={handlePull}
              onRemove={handleRemove}
              pullingId={pullModel.isPending ? pullModel.variables?.modelId : undefined}
              removingId={removeModel.isPending ? removeModel.variables : undefined}
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
