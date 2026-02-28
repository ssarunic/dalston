import { useNavigate, useLocation } from 'react-router-dom'
import { Mic, Loader2 } from 'lucide-react'
import { useLiveSession } from '@/contexts/LiveSessionContext'
import { cn } from '@/lib/utils'

function formatDuration(seconds: number): string {
  const mins = Math.floor(seconds / 60)
  const secs = seconds % 60
  return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`
}

export function LiveSessionIndicator() {
  const navigate = useNavigate()
  const location = useLocation()
  const { state, durationSeconds, wordCount } = useLiveSession()

  const isActive = state === 'recording' || state === 'connecting' || state === 'stopping'
  const isOnLivePage = location.pathname === '/realtime/live'

  // Don't show if not active or already on the live page
  if (!isActive || isOnLivePage) {
    return null
  }

  const handleClick = () => {
    navigate('/realtime/live')
  }

  return (
    <button
      onClick={handleClick}
      className={cn(
        'fixed bottom-6 right-6 z-50',
        'flex items-center gap-3 px-5 py-3 rounded-full',
        'bg-card border-2 shadow-xl',
        'hover:scale-105 transition-all cursor-pointer',
        'text-base font-medium',
        state === 'recording'
          ? 'border-red-500/50 shadow-red-500/20'
          : 'border-border'
      )}
    >
      {state === 'recording' ? (
        <>
          <span className="relative flex h-4 w-4">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75" />
            <span className="relative inline-flex rounded-full h-4 w-4 bg-red-500" />
          </span>
          <Mic className="h-5 w-5 text-red-500" />
          <span className="text-foreground font-semibold">{formatDuration(durationSeconds)}</span>
          <span className="text-muted-foreground">{wordCount} words</span>
        </>
      ) : state === 'connecting' ? (
        <>
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          <span className="text-muted-foreground">Connecting...</span>
        </>
      ) : (
        <>
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          <span className="text-muted-foreground">Finishing...</span>
        </>
      )}
    </button>
  )
}
