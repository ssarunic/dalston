import { useState } from 'react'
import { Key, Plus, Trash2, AlertCircle } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { Dialog } from '@/components/ui/dialog'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useApiKeys, useRevokeApiKey } from '@/hooks/useApiKeys'
import { CreateKeyDialog } from '@/components/CreateKeyDialog'
import { KeyCreatedModal } from '@/components/KeyCreatedModal'
import type { APIKey, APIKeyCreatedResponse } from '@/api/types'

const SCOPE_COLORS: Record<string, string> = {
  admin: 'bg-red-500/10 text-red-500 border-red-500/20',
  'jobs:read': 'bg-blue-500/10 text-blue-500 border-blue-500/20',
  'jobs:write': 'bg-green-500/10 text-green-500 border-green-500/20',
  realtime: 'bg-purple-500/10 text-purple-500 border-purple-500/20',
  webhooks: 'bg-orange-500/10 text-orange-500 border-orange-500/20',
}

function ScopeBadge({ scope }: { scope: string }) {
  const colorClass = SCOPE_COLORS[scope] || 'bg-gray-500/10 text-gray-500 border-gray-500/20'
  return (
    <Badge variant="outline" className={`text-xs ${colorClass}`}>
      {scope}
    </Badge>
  )
}

function formatTimeAgo(dateStr: string): string {
  const date = new Date(dateStr)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)

  if (diffMins < 1) return 'just now'
  if (diffMins < 60) return `${diffMins}m ago`
  const diffHours = Math.floor(diffMins / 60)
  if (diffHours < 24) return `${diffHours}h ago`
  const diffDays = Math.floor(diffHours / 24)
  if (diffDays < 30) return `${diffDays}d ago`
  const diffMonths = Math.floor(diffDays / 30)
  return `${diffMonths}mo ago`
}

export function ApiKeys() {
  const [showRevoked, setShowRevoked] = useState(false)
  const [createDialogOpen, setCreateDialogOpen] = useState(false)
  const [createdKey, setCreatedKey] = useState<APIKeyCreatedResponse | null>(null)
  const [revokeConfirm, setRevokeConfirm] = useState<APIKey | null>(null)
  const [revokeError, setRevokeError] = useState<string | null>(null)

  const { data, isLoading, error } = useApiKeys(showRevoked)
  const revokeApiKey = useRevokeApiKey()

  const handleKeyCreated = (key: APIKeyCreatedResponse) => {
    setCreateDialogOpen(false)
    setCreatedKey(key)
  }

  const handleRevoke = async (key: APIKey) => {
    setRevokeError(null)
    try {
      await revokeApiKey.mutateAsync(key.id)
      setRevokeConfirm(null)
    } catch (err) {
      if (err instanceof Error) {
        // Try to extract error detail from response
        const message = err.message.includes('Cannot revoke your own')
          ? 'Cannot revoke the API key you are currently using'
          : err.message
        setRevokeError(message)
      } else {
        setRevokeError('Failed to revoke key')
      }
    }
  }

  const keys = data?.keys ?? []

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">API Keys</h1>
          <p className="text-muted-foreground">Manage API keys for authentication</p>
        </div>
        <Button onClick={() => setCreateDialogOpen(true)}>
          <Plus className="h-4 w-4 mr-2" />
          Create Key
        </Button>
      </div>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="flex items-center gap-2">
            <Key className="h-5 w-5" />
            API Keys
          </CardTitle>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={showRevoked}
              onChange={(e) => setShowRevoked(e.target.checked)}
              className="rounded border-input"
            />
            Show revoked
          </label>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-3">
              {[...Array(3)].map((_, i) => (
                <Skeleton key={i} className="h-16 w-full" />
              ))}
            </div>
          ) : error ? (
            <div className="flex items-center gap-2 text-destructive py-4">
              <AlertCircle className="h-4 w-4" />
              <span>Failed to load API keys</span>
            </div>
          ) : keys.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              <Key className="h-12 w-12 mx-auto mb-4 opacity-50" />
              <p>No API keys found</p>
              <p className="text-sm mt-1">Create a key to get started</p>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Prefix</TableHead>
                  <TableHead>Name</TableHead>
                  <TableHead>Scopes</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead>Last Used</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {keys.map((key) => (
                  <TableRow
                    key={key.id}
                    className={key.is_revoked ? 'opacity-50' : undefined}
                  >
                    <TableCell className="font-mono text-sm">
                      {key.prefix}...
                      {key.is_current && (
                        <Badge variant="secondary" className="ml-2 text-xs">
                          current
                        </Badge>
                      )}
                      {key.is_revoked && (
                        <Badge variant="destructive" className="ml-2 text-xs">
                          revoked
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell>{key.name}</TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1">
                        {key.scopes.map((scope) => (
                          <ScopeBadge key={scope} scope={scope} />
                        ))}
                      </div>
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {formatTimeAgo(key.created_at)}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {key.last_used_at ? formatTimeAgo(key.last_used_at) : 'Never'}
                    </TableCell>
                    <TableCell className="text-right">
                      {!key.is_revoked && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setRevokeConfirm(key)}
                          className="text-red-400 hover:text-red-300 hover:bg-red-950"
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Create Key Dialog */}
      <CreateKeyDialog
        open={createDialogOpen}
        onOpenChange={setCreateDialogOpen}
        onCreated={handleKeyCreated}
      />

      {/* Key Created Modal */}
      <KeyCreatedModal
        keyData={createdKey}
        onClose={() => setCreatedKey(null)}
      />

      {/* Revoke Confirmation Dialog */}
      <Dialog
        open={revokeConfirm !== null}
        onOpenChange={(open) => {
          if (!open) {
            setRevokeConfirm(null)
            setRevokeError(null)
          }
        }}
      >
        <Card className="w-full max-w-md mx-4">
          <CardHeader>
            <CardTitle className="text-destructive">Revoke API Key</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-muted-foreground">
              Are you sure you want to revoke this API key? This action cannot be undone.
            </p>
            {revokeConfirm && (
              <div className="bg-muted p-3 rounded-md">
                <p className="font-mono text-sm">{revokeConfirm.prefix}...</p>
                <p className="text-sm text-muted-foreground">{revokeConfirm.name}</p>
              </div>
            )}
            {revokeError && (
              <div className="flex items-center gap-2 text-sm text-destructive">
                <AlertCircle className="h-4 w-4" />
                <span>{revokeError}</span>
              </div>
            )}
            <div className="flex justify-end gap-2">
              <Button
                variant="outline"
                onClick={() => {
                  setRevokeConfirm(null)
                  setRevokeError(null)
                }}
              >
                Cancel
              </Button>
              <Button
                variant="destructive"
                onClick={() => revokeConfirm && handleRevoke(revokeConfirm)}
                disabled={revokeApiKey.isPending}
              >
                {revokeApiKey.isPending ? 'Revoking...' : 'Revoke Key'}
              </Button>
            </div>
          </CardContent>
        </Card>
      </Dialog>
    </div>
  )
}
