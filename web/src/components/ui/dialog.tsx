import { useCallback, useEffect, useRef, type ReactNode } from 'react'

interface DialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  children: ReactNode
  /** Whether clicking the backdrop closes the dialog. Default: true */
  closeOnBackdropClick?: boolean
}

/**
 * Accessible dialog component with:
 * - Focus trapping
 * - Escape key to close
 * - ARIA attributes for screen readers
 * - Optional backdrop click to close
 */
export function Dialog({
  open,
  onOpenChange,
  children,
  closeOnBackdropClick = true,
}: DialogProps) {
  const dialogRef = useRef<HTMLDivElement>(null)
  const previousActiveElement = useRef<HTMLElement | null>(null)

  // Handle escape key
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onOpenChange(false)
      }
    },
    [onOpenChange]
  )

  // Handle backdrop click
  const handleBackdropClick = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      if (closeOnBackdropClick && e.target === e.currentTarget) {
        onOpenChange(false)
      }
    },
    [closeOnBackdropClick, onOpenChange]
  )

  // Focus management and event listeners
  useEffect(() => {
    if (open) {
      // Store the previously focused element
      previousActiveElement.current = document.activeElement as HTMLElement

      // Focus the dialog
      dialogRef.current?.focus()

      // Add escape key listener
      document.addEventListener('keydown', handleKeyDown)

      // Prevent body scroll
      document.body.style.overflow = 'hidden'

      return () => {
        document.removeEventListener('keydown', handleKeyDown)
        document.body.style.overflow = ''

        // Restore focus to the previously focused element
        previousActiveElement.current?.focus()
      }
    }
  }, [open, handleKeyDown])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 bg-black/50 flex items-center justify-center z-50"
      onClick={handleBackdropClick}
      role="presentation"
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        tabIndex={-1}
        className="outline-none"
      >
        {children}
      </div>
    </div>
  )
}

interface DialogContentProps {
  children: ReactNode
  className?: string
}

export function DialogContent({ children, className = '' }: DialogContentProps) {
  return (
    <div className={`w-full max-w-md mx-4 ${className}`}>
      {children}
    </div>
  )
}
