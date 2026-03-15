import { useState, useMemo } from 'react'
import {
  RefreshCw,
  AlertCircle,
  Loader2,
  Package,
  Plus,
} from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { S } from '@/lib/strings'
import {
  useModelRegistry,
  usePullModel,
  usePurgeModel,
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
  const purgeModel = usePurgeModel()
  const resolveHF = useResolveHFModel()
  const syncModels = useSyncModels()

  // Fetch unfiltered registry to derive available engine IDs for the filter dropdown
  const { data: allData } = useModelRegistry()
  const allModels = allData?.data
  const availableEngineIds = useMemo(() => {
    if (!allModels) return []
    return [...new Set(allModels.map((m) => m.engine_id))]
  }, [allModels])

  const models = useMemo(() => data?.data ?? [], [data?.data])

  // Client-side search filtering
  const filteredModels = useMemo(() => {
    if (!filters.search) return models
    const search = filters.search.toLowerCase()
    return models.filter(
      (m) =>
        m.id.toLowerCase().includes(search) ||
        m.name?.toLowerCase().includes(search) ||
        m.engine_id.toLowerCase().includes(search)
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

  const handlePurge = (modelId: string) => {
    purgeModel.mutate(modelId)
  }

  if (error) {
    return (
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold">{S.models.title}</h1>
            <p className="text-muted-foreground">
              {S.models.subtitle}
            </p>
          </div>
        </div>
        <div className="text-center py-12">
          <AlertCircle className="h-12 w-12 mx-auto mb-4 text-red-500" />
          <p className="text-red-500">{S.errors.failedToLoadModels}</p>
          <p className="text-sm text-muted-foreground mt-1">{error.message}</p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">{S.models.title}</h1>
          <p className="text-muted-foreground">
            {S.models.subtitle}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            onClick={() => syncModels.mutate()}
            disabled={syncModels.isPending}
          >
            <RefreshCw className={cn('h-4 w-4 mr-2', syncModels.isPending && 'animate-spin')} />
            {S.models.syncWithDisk}
          </Button>
          <Button onClick={() => {
            resolveHF.reset()
            setAddDialogOpen(true)
          }}>
            <Plus className="h-4 w-4 mr-2" />
            {S.models.addFromHF}
          </Button>
        </div>
      </div>

      {/* Models Card */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-4">
          <CardTitle className="text-base font-medium">{S.models.cardTitle}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Filters */}
          <ModelFiltersBar filters={filters} onChange={setFilters} availableEngineIds={availableEngineIds} />

          {/* Loading State */}
          {isLoading && (
            <div className="text-center py-12">
              <Loader2 className="h-8 w-8 animate-spin mx-auto mb-4 text-muted-foreground" />
              <p className="text-muted-foreground">{S.models.loadingModels}</p>
            </div>
          )}

          {/* Empty State */}
          {!isLoading && models.length === 0 && (
            <div className="text-center py-12">
              <Package className="h-12 w-12 mx-auto mb-4 opacity-50" />
              <p className="text-muted-foreground">{S.models.noModelsFound}</p>
              <p className="text-sm text-muted-foreground mt-1">
                {S.models.noModelsHint}
              </p>
            </div>
          )}

          {/* Models Table */}
          {!isLoading && filteredModels.length > 0 && (
            <ModelTable
              models={filteredModels}
              onPull={handlePull}
              onRemove={handleRemove}
              onPurge={handlePurge}
              pullingId={pullModel.isPending ? pullModel.variables?.modelId : undefined}
              removingId={removeModel.isPending ? removeModel.variables : undefined}
              purgingId={purgeModel.isPending ? purgeModel.variables : undefined}
            />
          )}
        </CardContent>
      </Card>

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
