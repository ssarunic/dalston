import { Link } from 'react-router-dom'
import {
  Globe,
  Zap,
  CheckCircle,
  XCircle,
  ArrowRight,
} from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useSystemCapabilities } from '@/hooks/useCapabilities'
import { cn } from '@/lib/utils'

const FEATURES = [
  { key: 'word_timestamps', label: 'Word Timestamps' },
  { key: 'speaker_diarization', label: 'Speaker Diarization' },
  { key: 'pii_detection', label: 'PII Detection' },
  { key: 'streaming', label: 'Streaming' },
] as const

export function CapabilitiesCard() {
  const { data: capabilities, isLoading } = useSystemCapabilities()

  if (isLoading) {
    return (
      <Card>
        <CardContent className="py-4 space-y-3">
          <div className="flex items-center justify-between">
            <Skeleton className="h-5 w-40" />
            <Skeleton className="h-9 w-24" />
          </div>
          <div className="flex gap-6">
            <Skeleton className="h-5 w-24" />
            <Skeleton className="h-5 w-32" />
            <Skeleton className="h-5 w-32" />
          </div>
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
      <CardContent className="py-4 space-y-3">
        {/* Header row: Title + Models + Button */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Zap className="h-4 w-4" />
            <span className="font-semibold">System Capabilities</span>
          </div>
          <div className="flex items-center gap-4">
            <div className="text-sm">
              <span className="text-muted-foreground">Models: </span>
              <span className="font-medium">
                {capabilities.models_ready}/{capabilities.models_total}
              </span>
            </div>
            <Button variant="outline" size="sm" asChild>
              <Link to="/models" className="inline-flex items-center gap-2">
                View All
                <ArrowRight className="h-4 w-4" />
              </Link>
            </Button>
          </div>
        </div>

        {/* Features row: Languages + all features */}
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
          {/* Languages */}
          <div className="flex items-center gap-2">
            <Globe className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm">
              {languageCount === 'All' ? 'Multilingual' : `${languageCount} languages`}
            </span>
          </div>

          {/* Features */}
          {FEATURES.map(({ key, label }) => {
            const isEnabled = capabilities.features[key]
            return (
              <div
                key={key}
                className={cn(
                  'flex items-center gap-1.5 text-sm',
                  isEnabled ? 'text-foreground' : 'text-muted-foreground'
                )}
              >
                {isEnabled ? (
                  <CheckCircle className="h-4 w-4 text-green-500 shrink-0" />
                ) : (
                  <XCircle className="h-4 w-4 text-muted-foreground shrink-0" />
                )}
                <span>{label}</span>
              </div>
            )
          })}
        </div>
      </CardContent>
    </Card>
  )
}
