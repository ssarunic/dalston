import { useNavigate } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { Button } from './ui/button'

interface BackButtonProps {
  fallbackPath?: string
  label?: string
  variant?: 'icon' | 'link' | 'outline'
  className?: string
}

export function BackButton({
  fallbackPath,
  label,
  variant = 'icon',
  className,
}: BackButtonProps) {
  const navigate = useNavigate()

  const handleBack = () => {
    // Check if there's history to go back to
    if (window.history.length > 1) {
      navigate(-1)
    } else if (fallbackPath) {
      navigate(fallbackPath)
    }
  }

  if (variant === 'link') {
    return (
      <button
        onClick={handleBack}
        className={`flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground ${className || ''}`}
      >
        <ArrowLeft className="h-4 w-4" />
        {label}
      </button>
    )
  }

  if (variant === 'outline') {
    return (
      <Button variant="outline" onClick={handleBack} className={className}>
        {label}
      </Button>
    )
  }

  return (
    <Button variant="ghost" size="icon" onClick={handleBack} className={className}>
      <ArrowLeft className="h-4 w-4" />
    </Button>
  )
}
