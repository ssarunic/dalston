import { Server, Radio } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { useEngines } from '@/hooks/useEngines'
import type { BatchEngine, WorkerStatus } from '@/api/types'

function StatusDot({ status }: { status: string }) {
  const color =
    status === 'healthy' || status === 'ready'
      ? 'bg-green-500'
      : status === 'busy'
        ? 'bg-yellow-500'
        : 'bg-red-500'
  return <span className={`inline-block w-2 h-2 rounded-full ${color}`} />
}

function BatchEngineRow({ engine }: { engine: BatchEngine }) {
  return (
    <div className="flex items-center justify-between py-3 px-2 rounded-md hover:bg-accent">
      <div className="flex items-center gap-3">
        <StatusDot status={engine.status} />
        <div>
          <div className="font-medium">{engine.engine_id}</div>
          <div className="text-sm text-muted-foreground">{engine.stage}</div>
        </div>
      </div>
      <div className="text-right">
        <div className="text-sm font-medium">{engine.queue_depth} queued</div>
        <div className="text-xs text-muted-foreground">
          {engine.processing} processing
        </div>
      </div>
    </div>
  )
}

function RealtimeWorkerRow({ worker }: { worker: WorkerStatus }) {
  return (
    <div className="flex items-center justify-between py-3 px-2 rounded-md hover:bg-accent">
      <div className="flex items-center gap-3">
        <StatusDot status={worker.status} />
        <div>
          <div className="font-medium">{worker.worker_id}</div>
          <div className="text-sm text-muted-foreground">{worker.endpoint}</div>
        </div>
      </div>
      <div className="text-right">
        <div className="text-sm font-medium">
          {worker.active_sessions}/{worker.capacity} sessions
        </div>
        <div className="text-xs text-muted-foreground">
          {worker.models.join(', ') || 'no models'}
        </div>
      </div>
    </div>
  )
}

export function Engines() {
  const { data, isLoading, error } = useEngines()

  const batchEngines = data?.batch_engines ?? []
  const realtimeWorkers = data?.realtime_engines ?? []

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Engines</h1>
        <p className="text-muted-foreground">
          Batch processing engines and realtime workers
        </p>
      </div>

      {error && (
        <div className="p-4 bg-destructive/10 text-destructive rounded-md">
          Failed to load engine status
        </div>
      )}

      <div className="grid gap-6 md:grid-cols-2">
        {/* Batch Engines */}
        <Card>
          <CardHeader className="flex flex-row items-center gap-2">
            <Server className="h-5 w-5 text-muted-foreground" />
            <CardTitle>Batch Engines</CardTitle>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <div className="space-y-3">
                {[...Array(4)].map((_, i) => (
                  <Skeleton key={i} className="h-14 w-full" />
                ))}
              </div>
            ) : batchEngines.length === 0 ? (
              <p className="text-sm text-muted-foreground py-4 text-center">
                No batch engines registered
              </p>
            ) : (
              <div className="divide-y divide-border">
                {batchEngines.map((engine) => (
                  <BatchEngineRow key={engine.engine_id} engine={engine} />
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Realtime Workers */}
        <Card>
          <CardHeader className="flex flex-row items-center gap-2">
            <Radio className="h-5 w-5 text-muted-foreground" />
            <CardTitle>Realtime Workers</CardTitle>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <div className="space-y-3">
                {[...Array(2)].map((_, i) => (
                  <Skeleton key={i} className="h-14 w-full" />
                ))}
              </div>
            ) : realtimeWorkers.length === 0 ? (
              <p className="text-sm text-muted-foreground py-4 text-center">
                No realtime workers registered
              </p>
            ) : (
              <div className="divide-y divide-border">
                {realtimeWorkers.map((worker) => (
                  <RealtimeWorkerRow key={worker.worker_id} worker={worker} />
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
