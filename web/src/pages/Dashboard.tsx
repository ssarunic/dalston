import { Link } from 'react-router-dom'
import { Activity, Cpu, Radio, CheckCircle } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { StatusBadge } from '@/components/StatusBadge'
import { useDashboard } from '@/hooks/useDashboard'
import type { ConsoleJobSummary } from '@/api/types'

function StatCard({
  title,
  value,
  subtitle,
  icon: Icon,
  loading,
}: {
  title: string
  value: string | number
  subtitle?: string
  icon: React.ElementType
  loading?: boolean
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        <Icon className="h-4 w-4 text-muted-foreground" />
      </CardHeader>
      <CardContent>
        {loading ? (
          <Skeleton className="h-8 w-20" />
        ) : (
          <>
            <div className="text-2xl font-bold">{value}</div>
            {subtitle && (
              <p className="text-xs text-muted-foreground">{subtitle}</p>
            )}
          </>
        )}
      </CardContent>
    </Card>
  )
}

function RecentJobRow({ job }: { job: ConsoleJobSummary }) {
  const timeAgo = formatTimeAgo(job.created_at)

  return (
    <Link
      to={`/jobs/${job.id}`}
      className="flex items-center justify-between py-3 px-2 rounded-md hover:bg-accent transition-colors"
    >
      <div className="flex items-center gap-4">
        <StatusBadge status={job.status} />
        <span className="text-sm font-mono text-muted-foreground">
          {job.id.slice(0, 8)}...
        </span>
      </div>
      <span className="text-sm text-muted-foreground">{timeAgo}</span>
    </Link>
  )
}

function formatTimeAgo(dateStr: string): string {
  const date = new Date(dateStr)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)

  if (diffMins < 1) return 'just now'
  if (diffMins < 60) return `${diffMins}m ago`
  const diffHours = Math.floor(diffMins / 60)
  if (diffHours < 24) return `${diffHours}h ago`
  const diffDays = Math.floor(diffHours / 24)
  return `${diffDays}d ago`
}

export function Dashboard() {
  const { health, jobStats, realtime, recentJobs, isLoading, error } = useDashboard()

  // Handle error state - show basic dashboard with error indicator
  const isHealthy = !error && health?.status === 'healthy'

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">Dashboard</h1>
        <p className="text-muted-foreground">System overview and recent activity</p>
      </div>

      {/* Status Cards */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <StatCard
          title="System Status"
          value={isLoading ? '...' : isHealthy ? 'Online' : 'Offline'}
          subtitle={health?.version ? `v${health.version}` : undefined}
          icon={Activity}
          loading={isLoading}
        />
        <StatCard
          title="Running Jobs"
          value={jobStats?.running ?? 0}
          subtitle={`${jobStats?.queued ?? 0} queued`}
          icon={Cpu}
          loading={isLoading}
        />
        <StatCard
          title="Realtime Sessions"
          value={`${realtime?.active_sessions ?? 0}/${realtime?.total_capacity ?? 0}`}
          subtitle={`${realtime?.worker_count ?? 0} workers`}
          icon={Radio}
          loading={isLoading}
        />
        <StatCard
          title="Completed Today"
          value={jobStats?.completed_today ?? 0}
          subtitle={jobStats?.failed_today ? `${jobStats.failed_today} failed` : 'no failures'}
          icon={CheckCircle}
          loading={isLoading}
        />
      </div>

      {/* Recent Jobs */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>Recent Jobs</CardTitle>
          <Link
            to="/jobs"
            className="text-sm text-primary hover:underline"
          >
            View all
          </Link>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-3">
              {[...Array(5)].map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : recentJobs.length === 0 ? (
            <p className="text-sm text-muted-foreground py-4 text-center">
              No jobs yet
            </p>
          ) : (
            <div className="divide-y divide-border">
              {recentJobs.map((job) => (
                <RecentJobRow key={job.id} job={job} />
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
