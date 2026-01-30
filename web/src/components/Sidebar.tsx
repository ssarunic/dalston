import { NavLink } from 'react-router-dom'
import { cn } from '@/lib/utils'
import {
  LayoutDashboard,
  ListTodo,
  Radio,
  Server,
} from 'lucide-react'

const navItems = [
  { to: '/', icon: LayoutDashboard, label: 'Dashboard' },
  { to: '/jobs', icon: ListTodo, label: 'Batch Jobs' },
  { to: '/realtime', icon: Radio, label: 'Realtime' },
  { to: '/engines', icon: Server, label: 'Engines' },
]

export function Sidebar() {
  return (
    <aside className="w-64 border-r border-border bg-card">
      <div className="p-6">
        <h1 className="text-xl font-bold text-foreground">DALSTON</h1>
        <p className="text-sm text-muted-foreground">Transcription Console</p>
      </div>
      <nav className="px-3">
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/'}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors',
                isActive
                  ? 'bg-accent text-accent-foreground'
                  : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground'
              )
            }
          >
            <item.icon className="h-4 w-4" />
            {item.label}
          </NavLink>
        ))}
      </nav>
    </aside>
  )
}
