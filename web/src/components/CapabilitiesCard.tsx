import { Link } from 'react-router-dom'
import {
  Globe,
  Zap,
  Clock,
  Users,
  Shield,
  Radio,
  CheckCircle,
  XCircle,
  ArrowRight,
} from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useSystemCapabilities } from '@/hooks/useCapabilities'
import { cn } from '@/lib/utils'

const FEATURES = [
  { key: 'word_timestamps', label: 'Word Timestamps', icon: Clock },
  { key: 'speaker_diarization', label: 'Speaker Diarization', icon: Users },
  { key: 'pii_detection', label: 'PII Detection', icon: Shield },
  { key: 'streaming', label: 'Real-time Streaming', icon: Radio },
] as const

export function CapabilitiesCard() {
  const { data: capabilities, isLoading } = useSystemCapabilities()

  if (isLoading) {
    return (
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base flex items-center gap-2">
            <Zap className="h-4 w-4" />
            System Capabilities
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <Skeleton className="h-6 w-32" />
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-4 w-24" />
        </CardContent>
      </Card>
    )
  }

  if (!capabilities) return null

  const languageCount = capabilities.languages.includes('*')
    ? 'All'
    : capabilities.languages.length

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-base flex items-center gap-2">
          <Zap className="h-4 w-4" />
          System Capabilities
        </CardTitle>
      </CardHeader>

      <CardContent className="space-y-4">
        {/* Languages */}
        <div>
          <p className="text-sm text-muted-foreground mb-1">Languages</p>
          <div className="flex items-center gap-2">
            <Globe className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm font-medium">
              {languageCount === 'All' ? 'Multilingual' : `${languageCount} supported`}
            </span>
            {!capabilities.languages.includes('*') && capabilities.languages.length > 0 && (
              <span className="text-xs text-muted-foreground">
                ({capabilities.languages.slice(0, 5).join(', ')}
                {capabilities.languages.length > 5 && '...'})
              </span>
            )}
          </div>
        </div>

        {/* Features */}
        <div>
          <p className="text-sm text-muted-foreground mb-2">Features</p>
          <div className="grid grid-cols-2 gap-2">
            {FEATURES.map(({ key, label }) => {
              const isEnabled = capabilities.features[key]
              return (
                <div
                  key={key}
                  className={cn(
                    'flex items-center gap-2 text-sm',
                    isEnabled ? 'text-foreground' : 'text-muted-foreground'
                  )}
                >
                  {isEnabled ? (
                    <CheckCircle className="h-4 w-4 text-green-500 shrink-0" />
                  ) : (
                    <XCircle className="h-4 w-4 text-muted-foreground shrink-0" />
                  )}
                  <span className="truncate">{label}</span>
                </div>
              )
            })}
          </div>
        </div>

        {/* Models */}
        <div className="flex items-center justify-between text-sm">
          <span className="text-muted-foreground">Models Ready</span>
          <span className="font-medium">
            {capabilities.models_ready} / {capabilities.models_total}
          </span>
        </div>

        <Button variant="outline" size="sm" className="w-full" asChild>
          <Link to="/models" className="inline-flex items-center justify-center gap-2">
            View All Models
            <ArrowRight className="h-4 w-4 shrink-0" />
          </Link>
        </Button>
      </CardContent>
    </Card>
  )
}
