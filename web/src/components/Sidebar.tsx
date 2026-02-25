import { NavLink, useNavigate } from 'react-router-dom'
import { cn } from '@/lib/utils'
import { useAuth } from '@/contexts/AuthContext'
import {
  LayoutDashboard,
  ListTodo,
  Radio,
  Server,
  Key,
  Webhook,
  ScrollText,
  Settings,
  LogOut,
} from 'lucide-react'

const navItems = [
  { to: '/', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/jobs', icon: ListTodo, label: 'Batch Jobs' },
  { to: '/realtime', icon: Radio, label: 'Realtime' },
  { to: '/engines', icon: Server, label: 'Engines' },
  { to: '/keys', icon: Key, label: 'API Keys' },
  { to: '/webhooks', icon: Webhook, label: 'Webhooks' },
  { to: '/audit', icon: ScrollText, label: 'Audit Log' },
  { to: '/settings', icon: Settings, label: 'Settings' },
]

interface SidebarProps {
  onNavigate?: () => void
  className?: string
}

export function Sidebar({ onNavigate, className }: SidebarProps) {
  const { logout, apiKey } = useAuth()
  const navigate = useNavigate()

  const handleLogout = () => {
    logout()
    navigate('/login')
    onNavigate?.()
  }

  const handleNavClick = () => {
    onNavigate?.()
  }

  // Show masked API key prefix
  const keyPrefix = apiKey ? `${apiKey.slice(0, 10)}...` : ''

  return (
    <aside className={cn('w-64 h-full border-r border-border bg-card flex flex-col', className)}>
      <div className="p-6">
        <h1 className="text-xl font-bold text-foreground">DALSTON</h1>
        <p className="text-sm text-muted-foreground">Transcription Console</p>
      </div>
      <nav className="px-3 flex-1 overflow-y-auto">
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/'}
            onClick={handleNavClick}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors',
                isActive
                  ? 'bg-accent text-accent-foreground'
                  : 'text-muted-foreground hover:bg-slate-800/70 hover:text-foreground'
              )
            }
          >
            <item.icon className="h-4 w-4" />
            {item.label}
          </NavLink>
        ))}
      </nav>
      <div className="p-3 border-t border-border">
        <div className="px-3 py-2 text-xs text-muted-foreground font-mono">
          {keyPrefix}
        </div>
        <button
          onClick={handleLogout}
          className="flex items-center gap-3 rounded-md px-3 py-2 text-sm w-full text-muted-foreground hover:bg-slate-800/70 hover:text-foreground transition-colors"
        >
          <LogOut className="h-4 w-4" />
          Logout
        </button>
      </div>
    </aside>
  )
}
