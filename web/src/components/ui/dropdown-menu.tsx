import * as React from 'react'
import { cn } from '@/lib/utils'

interface DropdownMenuContextValue {
  open: boolean
  setOpen: (open: boolean) => void
}

const DropdownMenuContext = React.createContext<DropdownMenuContextValue | null>(null)

function useDropdownMenuContext() {
  const context = React.useContext(DropdownMenuContext)
  if (!context) {
    throw new Error('DropdownMenu components must be used within a DropdownMenu')
  }
  return context
}

interface DropdownMenuProps {
  children: React.ReactNode
}

export function DropdownMenu({ children }: DropdownMenuProps) {
  const [open, setOpen] = React.useState(false)

  return (
    <DropdownMenuContext.Provider value={{ open, setOpen }}>
      <div className={cn('relative inline-block', open && 'z-50')}>{children}</div>
    </DropdownMenuContext.Provider>
  )
}

interface DropdownMenuTriggerProps {
  asChild?: boolean
  children: React.ReactNode
}

export function DropdownMenuTrigger({ asChild, children }: DropdownMenuTriggerProps) {
  const { open, setOpen } = useDropdownMenuContext()

  const handleClick = (e: React.MouseEvent) => {
    e.preventDefault()
    setOpen(!open)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      setOpen(!open)
    }
    if (e.key === 'Escape' && open) {
      setOpen(false)
    }
  }

  if (asChild && React.isValidElement(children)) {
    return React.cloneElement(children as React.ReactElement<React.HTMLAttributes<HTMLElement>>, {
      onClick: handleClick,
      onKeyDown: handleKeyDown,
      'aria-expanded': open,
      'aria-haspopup': 'menu',
    })
  }

  return (
    <button
      type="button"
      onClick={handleClick}
      onKeyDown={handleKeyDown}
      aria-expanded={open}
      aria-haspopup="menu"
    >
      {children}
    </button>
  )
}

interface DropdownMenuContentProps {
  className?: string
  align?: 'start' | 'end'
  side?: 'bottom' | 'top'
  children: React.ReactNode
}

export function DropdownMenuContent({ className, align = 'start', side = 'bottom', children }: DropdownMenuContentProps) {
  const { open, setOpen } = useDropdownMenuContext()
  const ref = React.useRef<HTMLDivElement>(null)

  React.useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) {
        const parent = ref.current.parentElement
        const trigger = parent?.querySelector('button')
        if (trigger && trigger.contains(event.target as Node)) {
          return
        }
        setOpen(false)
      }
    }

    function handleEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setOpen(false)
      }
    }

    if (open) {
      document.addEventListener('mousedown', handleClickOutside)
      document.addEventListener('keydown', handleEscape)
      return () => {
        document.removeEventListener('mousedown', handleClickOutside)
        document.removeEventListener('keydown', handleEscape)
      }
    }
  }, [open, setOpen])

  if (!open) return null

  return (
    <div
      ref={ref}
      role="menu"
      className={cn(
        'absolute z-50 w-max min-w-[8rem] overflow-hidden rounded-md border bg-card p-1 text-card-foreground shadow-md',
        'animate-in fade-in-0 zoom-in-95',
        align === 'end' ? 'right-0' : 'left-0',
        side === 'bottom' ? 'mt-1' : 'bottom-full mb-1',
        className
      )}
    >
      {children}
    </div>
  )
}

interface DropdownMenuItemProps {
  className?: string
  disabled?: boolean
  onSelect?: () => void
  children: React.ReactNode
}

export function DropdownMenuItem({ className, disabled, onSelect, children }: DropdownMenuItemProps) {
  const { setOpen } = useDropdownMenuContext()

  const handleClick = () => {
    if (disabled) return
    onSelect?.()
    setOpen(false)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault()
      handleClick()
    }
  }

  return (
    <div
      role="menuitem"
      tabIndex={disabled ? -1 : 0}
      onClick={handleClick}
      onKeyDown={handleKeyDown}
      className={cn(
        'relative flex cursor-pointer select-none items-center rounded-sm px-2 py-1.5 text-sm outline-none transition-colors',
        'focus:bg-accent focus:text-accent-foreground',
        'hover:bg-accent hover:text-accent-foreground',
        disabled && 'pointer-events-none opacity-50',
        className
      )}
    >
      {children}
    </div>
  )
}

export function DropdownMenuSeparator() {
  return <div className="-mx-1 my-1 h-px bg-muted" />
}
