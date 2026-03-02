import { useState, useRef, useMemo, useCallback } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { Download, Loader2, CheckCircle, XCircle, X } from 'lucide-react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '@/api/client'
import { cn } from '@/lib/utils'
import type { ModelRegistryEntry } from '@/api/types'

interface Notification {
  id: string
  modelId: string
  type: 'completed' | 'failed'
  expiresAt: number
}

const POLL_INTERVAL_ACTIVE = 3000 // Poll every 3s when downloads active
const POLL_INTERVAL_IDLE = 30000 // Poll every 30s when idle
const NOTIFICATION_DURATION = 5000 // Auto-dismiss after 5s

let notificationCounter = 0

export function DownloadIndicator() {
  const navigate = useNavigate()
  const location = useLocation()
  const queryClient = useQueryClient()
  const [notifications, setNotifications] = useState<Notification[]>([])
  const previousDownloadsRef = useRef<Map<string, ModelRegistryEntry>>(new Map())
  const isInitializedRef = useRef(false)

  // Handle data changes and detect status transitions
  const handleDataChange = useCallback(
    (newData: ModelRegistryEntry[]) => {
      const previousMap = previousDownloadsRef.current

      // Skip notifications on initial load
      if (!isInitializedRef.current) {
        previousDownloadsRef.current = new Map(newData.map((m) => [m.id, m]))
        isInitializedRef.current = true
        return
      }

      const newNotifications: Notification[] = []
      const expiresAt = Date.now() + NOTIFICATION_DURATION

      for (const model of newData) {
        const prev = previousMap.get(model.id)
        if (prev?.status === 'downloading' && model.status !== 'downloading') {
          notificationCounter += 1
          newNotifications.push({
            id: `${model.id}-${notificationCounter}`,
            modelId: model.id,
            type: model.status === 'ready' ? 'completed' : 'failed',
            expiresAt,
          })
        }
      }

      if (newNotifications.length > 0) {
        setNotifications((prev) => [...prev, ...newNotifications])
        // Invalidate model registry queries to update Models page
        queryClient.invalidateQueries({ queryKey: ['modelRegistry'] })

        // Schedule auto-dismiss
        setTimeout(() => {
          const now = Date.now()
          setNotifications((prev) => prev.filter((n) => n.expiresAt > now))
        }, NOTIFICATION_DURATION + 100)
      }

      previousDownloadsRef.current = new Map(newData.map((m) => [m.id, m]))
    },
    [queryClient]
  )

  // Poll for all models to detect status transitions
  const { data } = useQuery({
    queryKey: ['downloadIndicator'],
    queryFn: async () => {
      const result = await apiClient.getModelRegistry()
      // Process in the query function to avoid useEffect
      handleDataChange(result.data)
      return result
    },
    refetchInterval: (query) => {
      const models = query.state.data?.data ?? []
      const hasDownloading = models.some((m) => m.status === 'downloading')
      return hasDownloading ? POLL_INTERVAL_ACTIVE : POLL_INTERVAL_IDLE
    },
  })

  const allModels = useMemo(() => data?.data ?? [], [data?.data])
  const downloads = useMemo(
    () => allModels.filter((m) => m.status === 'downloading'),
    [allModels]
  )
  const activeCount = downloads.length
  const isOnModelsPage = location.pathname === '/models'

  const dismissNotification = useCallback((id: string) => {
    setNotifications((prev) => prev.filter((n) => n.id !== id))
  }, [])

  const handleClick = useCallback(() => {
    navigate('/models')
  }, [navigate])

  // Calculate overall progress
  const overallProgress = useMemo(
    () =>
      downloads.length > 0
        ? Math.round(
            downloads.reduce((sum, d) => sum + (d.download_progress ?? 0), 0) / downloads.length
          )
        : 0,
    [downloads]
  )

  // Don't show floating indicator if on models page (they can see status there)
  // But still show notifications
  const showFloatingIndicator = activeCount > 0 && !isOnModelsPage

  return (
    <>
      {/* Floating indicator for active downloads */}
      {/* Positioned above LiveSessionIndicator which uses bottom-6 */}
      {showFloatingIndicator && (
        <button
          onClick={handleClick}
          className={cn(
            'fixed bottom-20 right-6 z-50',
            'flex items-center gap-3 px-5 py-3 rounded-full',
            'bg-card border-2 border-blue-500/50 shadow-xl shadow-blue-500/20',
            'hover:scale-105 transition-all cursor-pointer',
            'text-base font-medium'
          )}
        >
          <Loader2 className="h-5 w-5 animate-spin text-blue-500" />
          <Download className="h-5 w-5 text-blue-500" />
          <span className="text-foreground font-semibold">
            {activeCount} model{activeCount > 1 ? 's' : ''}
          </span>
          <span className="text-muted-foreground">{overallProgress}%</span>
        </button>
      )}

      {/* Toast notifications for completed/failed downloads */}
      {notifications.length > 0 && (
        <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2">
          {notifications.map((notification) => (
            <div
              key={notification.id}
              className={cn(
                'flex items-center gap-3 px-4 py-3 rounded-lg shadow-lg',
                'animate-in slide-in-from-right-5 fade-in duration-200',
                notification.type === 'completed'
                  ? 'bg-green-600 text-white'
                  : 'bg-red-600 text-white'
              )}
            >
              {notification.type === 'completed' ? (
                <CheckCircle className="h-5 w-5 flex-shrink-0" />
              ) : (
                <XCircle className="h-5 w-5 flex-shrink-0" />
              )}
              <span className="text-sm font-medium">
                {notification.type === 'completed' ? 'Downloaded' : 'Failed'}:{' '}
                <span className="font-mono">{notification.modelId}</span>
              </span>
              <button
                onClick={() => dismissNotification(notification.id)}
                className="ml-2 p-1 hover:bg-white/20 rounded"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          ))}
        </div>
      )}
    </>
  )
}
