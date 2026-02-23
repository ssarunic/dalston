import * as React from 'react'
import { cn } from '@/lib/utils'

interface TooltipProps {
  content: React.ReactNode
  side?: 'top' | 'bottom' | 'left' | 'right'
  delayMs?: number
  className?: string
  children: React.ReactNode
}

export function Tooltip({
  content,
  side = 'top',
  delayMs = 200,
  className,
  children,
}: TooltipProps) {
  const [visible, setVisible] = React.useState(false)
  const [position, setPosition] = React.useState({ top: 0, left: 0 })
  const triggerRef = React.useRef<HTMLDivElement>(null)
  const tooltipRef = React.useRef<HTMLDivElement>(null)
  const timeoutRef = React.useRef<number | undefined>(undefined)

  const updatePosition = React.useCallback(() => {
    if (!triggerRef.current || !tooltipRef.current) return

    const triggerRect = triggerRef.current.getBoundingClientRect()
    const tooltipRect = tooltipRef.current.getBoundingClientRect()
    const offset = 8

    let top = 0
    let left = 0

    switch (side) {
      case 'top':
        top = triggerRect.top - tooltipRect.height - offset
        left = triggerRect.left + (triggerRect.width - tooltipRect.width) / 2
        break
      case 'bottom':
        top = triggerRect.bottom + offset
        left = triggerRect.left + (triggerRect.width - tooltipRect.width) / 2
        break
      case 'left':
        top = triggerRect.top + (triggerRect.height - tooltipRect.height) / 2
        left = triggerRect.left - tooltipRect.width - offset
        break
      case 'right':
        top = triggerRect.top + (triggerRect.height - tooltipRect.height) / 2
        left = triggerRect.right + offset
        break
    }

    // Keep tooltip within viewport
    const padding = 8
    left = Math.max(padding, Math.min(left, window.innerWidth - tooltipRect.width - padding))
    top = Math.max(padding, Math.min(top, window.innerHeight - tooltipRect.height - padding))

    setPosition({ top, left })
  }, [side])

  const handleMouseEnter = () => {
    timeoutRef.current = window.setTimeout(() => {
      setVisible(true)
    }, delayMs)
  }

  const handleMouseLeave = () => {
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current)
    }
    setVisible(false)
  }

  const handleFocus = () => {
    setVisible(true)
  }

  const handleBlur = () => {
    setVisible(false)
  }

  React.useEffect(() => {
    if (visible) {
      // Small delay to let the tooltip render before measuring
      requestAnimationFrame(updatePosition)
    }
  }, [visible, updatePosition])

  React.useEffect(() => {
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current)
      }
    }
  }, [])

  return (
    <>
      <div
        ref={triggerRef}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={handleMouseLeave}
        onFocus={handleFocus}
        onBlur={handleBlur}
        className="inline-flex"
      >
        {children}
      </div>
      {visible && (
        <div
          ref={tooltipRef}
          role="tooltip"
          style={{
            position: 'fixed',
            top: position.top,
            left: position.left,
          }}
          className={cn(
            'z-50 overflow-hidden rounded-md border bg-popover px-3 py-1.5 text-sm text-popover-foreground shadow-md',
            'animate-in fade-in-0 zoom-in-95',
            className
          )}
        >
          {content}
        </div>
      )}
    </>
  )
}
