import { useState } from 'react'
import { Check, Copy, Eye, EyeOff, AlertTriangle } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import type { WebhookEndpointCreated } from '@/api/types'

interface WebhookSecretModalProps {
  webhook: WebhookEndpointCreated | null
  onClose: () => void
  isRotation?: boolean
}

export function WebhookSecretModal({ webhook, onClose, isRotation }: WebhookSecretModalProps) {
  const [showSecret, setShowSecret] = useState(false)
  const [copied, setCopied] = useState(false)

  if (!webhook) return null

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(webhook.signing_secret)
      setCopied(true)
    } catch {
      // Fallback for older browsers or restricted contexts
      const textArea = document.createElement('textarea')
      textArea.value = webhook.signing_secret
      textArea.style.position = 'fixed'
      textArea.style.left = '-9999px'
      document.body.appendChild(textArea)
      textArea.select()
      try {
        document.execCommand('copy')
        setCopied(true)
      } catch {
        console.error('Failed to copy to clipboard')
      }
      document.body.removeChild(textArea)
    }
    setTimeout(() => setCopied(false), 2000)
  }

  const maskedSecret =
    webhook.signing_secret.slice(0, 10) + '...' + webhook.signing_secret.slice(-4)

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <Card className="w-full max-w-lg mx-4">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-green-500">
            <Check className="h-5 w-5" />
            {isRotation ? 'Secret Rotated' : 'Webhook Created'}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Warning */}
          <div className="flex items-start gap-2 p-3 rounded-md bg-orange-500/10 border border-orange-500/20 text-sm">
            <AlertTriangle className="h-4 w-4 text-orange-500 mt-0.5" />
            <div>
              <p className="font-medium text-orange-500">Save this secret now</p>
              <p className="text-muted-foreground">
                This is the only time you will see the signing secret. Store it securely!
                {isRotation && ' The old secret is now invalid.'}
              </p>
            </div>
          </div>

          {/* Webhook Details */}
          <div className="space-y-3">
            <div>
              <p className="text-sm font-medium text-muted-foreground">URL</p>
              <p className="font-mono text-sm break-all">{webhook.url}</p>
            </div>

            <div>
              <p className="text-sm font-medium text-muted-foreground">Events</p>
              <div className="flex flex-wrap gap-1 mt-1">
                {webhook.events.map((event) => (
                  <Badge key={event} variant="secondary" className="text-xs">
                    {event}
                  </Badge>
                ))}
              </div>
            </div>

            {/* Signing Secret */}
            <div>
              <p className="text-sm font-medium text-muted-foreground mb-2">Signing Secret</p>
              <div className="flex items-center gap-2">
                <div className="flex-1 bg-muted p-3 rounded-md font-mono text-sm break-all">
                  {showSecret ? webhook.signing_secret : maskedSecret}
                </div>
                <div className="flex flex-col gap-1">
                  <Button
                    variant="outline"
                    size="icon"
                    onClick={() => setShowSecret(!showSecret)}
                    title={showSecret ? 'Hide secret' : 'Show secret'}
                  >
                    {showSecret ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </Button>
                  <Button
                    variant="outline"
                    size="icon"
                    onClick={handleCopy}
                    title="Copy to clipboard"
                  >
                    {copied ? (
                      <Check className="h-4 w-4 text-green-500" />
                    ) : (
                      <Copy className="h-4 w-4" />
                    )}
                  </Button>
                </div>
              </div>
            </div>
          </div>

          {/* Verification Example */}
          <div>
            <p className="text-sm font-medium text-muted-foreground mb-2">Signature Verification</p>
            <div className="bg-muted p-3 rounded-md">
              <code className="text-xs break-all">
                # Verify: sha256(timestamp + &quot;.&quot; + payload) using HMAC
                <br />
                X-Dalston-Signature: sha256=...
                <br />
                X-Dalston-Timestamp: 1234567890
              </code>
            </div>
          </div>

          {/* Close Button */}
          <div className="flex justify-end pt-2">
            <Button onClick={onClose}>Done</Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
