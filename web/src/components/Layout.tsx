import { useState, useRef } from 'react'
import { Menu } from 'lucide-react'
import { Sidebar } from './Sidebar'
import { LiveSessionIndicator } from './LiveSessionIndicator'
import { Sheet, SheetContent, SheetTrigger } from './ui/sheet'
import { Button } from './ui/button'
import { useScrollRestoration } from '@/hooks/useScrollRestoration'

interface LayoutProps {
  children: React.ReactNode
}

export function Layout({ children }: LayoutProps) {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const mainRef = useRef<HTMLElement>(null)

  useScrollRestoration(mainRef)

  return (
    <div className="flex h-screen bg-background">
      {/* Desktop sidebar - hidden on mobile */}
      <div className="hidden md:flex h-full">
        <Sidebar />
      </div>

      {/* Mobile header with hamburger menu */}
      <div className="flex flex-col flex-1 min-w-0">
        <header className="md:hidden flex items-center gap-4 border-b border-border px-4 py-3 bg-card">
          <Sheet open={sidebarOpen} onOpenChange={setSidebarOpen}>
            <SheetTrigger asChild>
              <Button variant="ghost" size="icon" className="shrink-0">
                <Menu className="h-5 w-5" />
                <span className="sr-only">Toggle navigation menu</span>
              </Button>
            </SheetTrigger>
            <SheetContent side="left" className="p-0 w-64">
              <Sidebar onNavigate={() => setSidebarOpen(false)} className="border-r-0" />
            </SheetContent>
          </Sheet>
          <div>
            <h1 className="text-lg font-bold text-foreground">DALSTON</h1>
          </div>
        </header>

        <main ref={mainRef} className="flex-1 min-w-0 overflow-y-auto overflow-x-hidden p-4 md:p-6">
          {children}
        </main>
      </div>

      {/* Floating indicator for active live sessions */}
      <LiveSessionIndicator />
    </div>
  )
}
