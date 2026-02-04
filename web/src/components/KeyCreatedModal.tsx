import { useState } from 'react'
import { Check, Copy, Eye, EyeOff, AlertTriangle } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import type { APIKeyCreatedResponse } from '@/api/types'

interface KeyCreatedModalProps {
  keyData: APIKeyCreatedResponse | null
  onClose: () => void
}

export function KeyCreatedModal({ keyData, onClose }: KeyCreatedModalProps) {
  const [showKey, setShowKey] = useState(false)
  const [copied, setCopied] = useState(false)

  if (!keyData) return null

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(keyData.key)
      setCopied(true)
    } catch {
      // Fallback for older browsers or restricted contexts
      const textArea = document.createElement('textarea')
      textArea.value = keyData.key
      textArea.style.position = 'fixed'
      textArea.style.left = '-9999px'
      document.body.appendChild(textArea)
      textArea.select()
      try {
        document.execCommand('copy')
        setCopied(true)
      } catch {
        // Copy failed - key is still visible for manual copy
        console.error('Failed to copy to clipboard')
      }
      document.body.removeChild(textArea)
    }
    setTimeout(() => setCopied(false), 2000)
  }

  const maskedKey = keyData.key.slice(0, 10) + '...' + keyData.key.slice(-4)

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <Card className="w-full max-w-lg mx-4">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-green-500">
            <Check className="h-5 w-5" />
            API Key Created
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Warning */}
          <div className="flex items-start gap-2 p-3 rounded-md bg-orange-500/10 border border-orange-500/20 text-sm">
            <AlertTriangle className="h-4 w-4 text-orange-500 mt-0.5" />
            <div>
              <p className="font-medium text-orange-500">Save this key now</p>
              <p className="text-muted-foreground">
                This is the only time you will see the full API key. Store it securely!
              </p>
            </div>
          </div>

          {/* Key Details */}
          <div className="space-y-3">
            <div>
              <p className="text-sm font-medium text-muted-foreground">Name</p>
              <p className="font-medium">{keyData.name}</p>
            </div>

            <div>
              <p className="text-sm font-medium text-muted-foreground">Scopes</p>
              <p className="font-mono text-sm">{keyData.scopes.join(', ')}</p>
            </div>

            {/* API Key */}
            <div>
              <p className="text-sm font-medium text-muted-foreground mb-2">API Key</p>
              <div className="flex items-center gap-2">
                <div className="flex-1 bg-muted p-3 rounded-md font-mono text-sm break-all">
                  {showKey ? keyData.key : maskedKey}
                </div>
                <div className="flex flex-col gap-1">
                  <Button
                    variant="outline"
                    size="icon"
                    onClick={() => setShowKey(!showKey)}
                    title={showKey ? 'Hide key' : 'Show key'}
                  >
                    {showKey ? (
                      <EyeOff className="h-4 w-4" />
                    ) : (
                      <Eye className="h-4 w-4" />
                    )}
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

          {/* Usage Example */}
          <div>
            <p className="text-sm font-medium text-muted-foreground mb-2">Usage</p>
            <div className="bg-muted p-3 rounded-md">
              <code className="text-xs break-all">
                curl -H "Authorization: Bearer {showKey ? keyData.key : maskedKey}" ...
              </code>
            </div>
          </div>

          {/* Close Button */}
          <div className="flex justify-end pt-2">
            <Button onClick={onClose}>
              Done
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
