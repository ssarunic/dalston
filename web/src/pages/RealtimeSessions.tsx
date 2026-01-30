import { useRealtimeStatus } from '@/hooks/useRealtimeStatus'

export function RealtimeSessions() {
  const { data, isLoading, error } = useRealtimeStatus()

  if (isLoading) return <div className="text-muted-foreground">Loading...</div>
  if (error) return <div className="text-red-400">Error loading realtime status</div>

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Realtime</h1>
      <p className="text-muted-foreground">
        Workers: {data?.capacity.worker_count ?? 0} |
        Capacity: {data?.capacity.used_capacity ?? 0}/{data?.capacity.total_capacity ?? 0}
      </p>
      <p className="text-muted-foreground mt-4">Full realtime view will be implemented later</p>
    </div>
  )
}
