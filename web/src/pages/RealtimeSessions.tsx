import { Radio } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { useRealtimeStatus } from '@/hooks/useRealtimeStatus'

function StatusDot({ status }: { status: string }) {
  const color =
    status === 'ready'
      ? 'bg-green-500'
      : status === 'at_capacity'
        ? 'bg-yellow-500'
        : 'bg-red-500'
  return <span className={`inline-block w-3 h-3 rounded-full ${color}`} />
}

export function RealtimeSessions() {
  const { data, isLoading, error } = useRealtimeStatus()

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Realtime</h1>
        <p className="text-muted-foreground">
          Real-time transcription workers and capacity
        </p>
      </div>

      {error && (
        <div className="p-4 bg-destructive/10 text-destructive rounded-md">
          Failed to load realtime status
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-3">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Status</CardTitle>
            <Radio className="h-4 w-4 text-muted-foreground" />
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <Skeleton className="h-8 w-24" />
            ) : (
              <div className="flex items-center gap-2">
                <StatusDot status={data?.status ?? 'unavailable'} />
                <span className="text-2xl font-bold capitalize">
                  {data?.status?.replace('_', ' ') ?? 'Unknown'}
                </span>
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Active Sessions</CardTitle>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <Skeleton className="h-8 w-20" />
            ) : (
              <>
                <div className="text-2xl font-bold">
                  {data?.active_sessions ?? 0} / {data?.total_capacity ?? 0}
                </div>
                <p className="text-xs text-muted-foreground">
                  {data?.available_capacity ?? 0} available
                </p>
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Workers</CardTitle>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <Skeleton className="h-8 w-16" />
            ) : (
              <>
                <div className="text-2xl font-bold">
                  {data?.ready_workers ?? 0} / {data?.worker_count ?? 0}
                </div>
                <p className="text-xs text-muted-foreground">ready</p>
              </>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Capacity Overview</CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <Skeleton className="h-4 w-full" />
          ) : (
            <div className="space-y-2">
              <div className="flex justify-between text-sm">
                <span>Used</span>
                <span>{data?.active_sessions ?? 0} / {data?.total_capacity ?? 0}</span>
              </div>
              <div className="h-2 bg-muted rounded-full overflow-hidden">
                <div
                  className="h-full bg-primary transition-all"
                  style={{
                    width: `${data?.total_capacity ? (data.active_sessions / data.total_capacity) * 100 : 0}%`,
                  }}
                />
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
