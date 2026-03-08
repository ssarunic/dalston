import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { KeyRound, AlertCircle } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { useAuth } from '@/contexts/AuthContext'
import { S } from '@/lib/strings'

export function Login() {
  const [apiKey, setApiKey] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const { login } = useAuth()
  const navigate = useNavigate()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)
    setIsLoading(true)

    const result = await login(apiKey.trim())

    setIsLoading(false)

    if (result.success) {
      navigate('/')
    } else {
      setError(result.error || S.errors.loginFailed)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-4">
      <Card className="w-full max-w-md">
        <CardHeader className="text-center">
          <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-primary/10">
            <KeyRound className="h-6 w-6 text-primary" />
          </div>
          <CardTitle className="text-2xl">{S.login.title}</CardTitle>
          <p className="text-sm text-muted-foreground mt-2">
            {S.login.instructions}
          </p>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-2">
              <label htmlFor="apiKey" className="text-sm font-medium">
                {S.login.apiKeyLabel}
              </label>
              <input
                id="apiKey"
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={S.login.apiKeyPlaceholder}
                className="w-full px-3 py-2 rounded-md border border-input bg-background text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                disabled={isLoading}
                autoFocus
              />
            </div>

            {error && (
              <div className="flex items-center gap-2 text-sm text-destructive">
                <AlertCircle className="h-4 w-4" />
                <span>{error}</span>
              </div>
            )}

            <Button type="submit" className="w-full" disabled={isLoading || !apiKey.trim()}>
              {isLoading ? S.login.validating : S.common.login}
            </Button>
          </form>

          <div className="mt-6 text-center text-xs text-muted-foreground">
            <p>
              {S.login.createKeyHint}{' '}
              <code className="bg-muted px-1 py-0.5 rounded">
                {S.login.createKeyCommand}
              </code>
            </p>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
