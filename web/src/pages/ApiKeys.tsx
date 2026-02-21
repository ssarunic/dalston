import { useMemo, useState } from 'react'
import { Key, Plus, Trash2, AlertCircle } from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { Dialog } from '@/components/ui/dialog'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { useMediaQuery } from '@/hooks/useMediaQuery'
import { useSharedTableState } from '@/hooks/useSharedTableState'
import { useApiKeys, useRevokeApiKey } from '@/hooks/useApiKeys'
import { CreateKeyDialog } from '@/components/CreateKeyDialog'
import { KeyCreatedModal } from '@/components/KeyCreatedModal'
import type { APIKey, APIKeyCreatedResponse } from '@/api/types'

const DEFAULT_LIMIT = 20
const LIMIT_OPTIONS = [20, 50, 100] as const
const STATUS_OPTIONS = [
  { label: 'Active', value: 'active' },
  { label: 'All', value: 'all' },
  { label: 'Revoked', value: 'revoked' },
] as const
const SORT_OPTIONS = [
  { label: 'Newest first', value: 'created_desc' },
  { label: 'Oldest first', value: 'created_asc' },
  { label: 'Last used first', value: 'last_used_desc' },
  { label: 'Least recently used', value: 'last_used_asc' },
] as const

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
  const isMobile = useMediaQuery('(max-width: 767px)')
  const {
    status,
    sort,
    limit,
    setStatus,
    setSort,
    setLimit,
  } = useSharedTableState({
    defaultStatus: 'active',
    statusOptions: STATUS_OPTIONS.map((option) => option.value),
    defaultSort: 'created_desc',
    sortOptions: SORT_OPTIONS.map((option) => option.value),
    defaultLimit: DEFAULT_LIMIT,
    limitOptions: LIMIT_OPTIONS,
  })
  const [createDialogOpen, setCreateDialogOpen] = useState(false)
  const [createdKey, setCreatedKey] = useState<APIKeyCreatedResponse | null>(null)
  const [revokeConfirm, setRevokeConfirm] = useState<APIKey | null>(null)
  const [revokeError, setRevokeError] = useState<string | null>(null)

  const { data, isLoading, error } = useApiKeys(status !== 'active')
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

  const keys = useMemo(() => data?.keys ?? [], [data])
  const filteredKeys = useMemo(() => {
    if (status === 'revoked') return keys.filter((key) => key.is_revoked)
    if (status === 'active') return keys.filter((key) => !key.is_revoked)
    return keys
  }, [keys, status])
  const visibleKeys = useMemo(() => {
    const sorted = [...filteredKeys]
    sorted.sort((a, b) => {
      if (sort.startsWith('last_used')) {
        const left = a.last_used_at ? new Date(a.last_used_at).getTime() : 0
        const right = b.last_used_at ? new Date(b.last_used_at).getTime() : 0
        return sort === 'last_used_asc' ? left - right : right - left
      }
      const left = new Date(a.created_at).getTime()
      const right = new Date(b.created_at).getTime()
      return sort === 'created_asc' ? left - right : right - left
    })
    return sorted.slice(0, limit)
  }, [filteredKeys, sort, limit])

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
          <div className="flex items-center gap-2">
            <Select value={status} onValueChange={setStatus}>
              <SelectTrigger className="w-[130px]">
                <SelectValue placeholder="Status" />
              </SelectTrigger>
              <SelectContent>
                {STATUS_OPTIONS.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={sort} onValueChange={setSort}>
              <SelectTrigger className="w-[170px]">
                <SelectValue placeholder="Sort" />
              </SelectTrigger>
              <SelectContent>
                {SORT_OPTIONS.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={String(limit)} onValueChange={(value) => setLimit(Number(value))}>
              <SelectTrigger className="w-[120px]">
                <SelectValue placeholder="Rows" />
              </SelectTrigger>
              <SelectContent>
                {LIMIT_OPTIONS.map((size) => (
                  <SelectItem key={size} value={String(size)}>
                    {size}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
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
          ) : visibleKeys.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              <Key className="h-12 w-12 mx-auto mb-4 opacity-50" />
              <p>No API keys found</p>
              <p className="text-sm mt-1">Try changing filters or create a key</p>
            </div>
          ) : (
            isMobile ? (
              <div className="space-y-3">
                {visibleKeys.map((key) => (
                  <div
                    key={key.id}
                    className={`rounded-lg border border-border p-3 ${key.is_revoked ? 'opacity-60' : ''}`}
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <p className="font-mono text-sm break-all">{key.prefix}...</p>
                        <p className="text-sm">{key.name}</p>
                      </div>
                      <div className="flex items-center gap-1">
                        {key.is_current && (
                          <Badge variant="secondary" className="text-xs">
                            current
                          </Badge>
                        )}
                        {key.is_revoked && (
                          <Badge variant="destructive" className="text-xs">
                            revoked
                          </Badge>
                        )}
                      </div>
                    </div>
                    <div className="mt-2 flex flex-wrap gap-1">
                      {key.scopes.map((scope) => (
                        <ScopeBadge key={scope} scope={scope} />
                      ))}
                    </div>
                    <div className="mt-3 grid grid-cols-2 gap-2 text-xs">
                      <div>
                        <p className="text-muted-foreground">Created</p>
                        <p>{formatTimeAgo(key.created_at)}</p>
                      </div>
                      <div>
                        <p className="text-muted-foreground">Last Used</p>
                        <p>{key.last_used_at ? formatTimeAgo(key.last_used_at) : 'Never'}</p>
                      </div>
                    </div>
                    {!key.is_revoked && (
                      <div className="mt-3 flex justify-end">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => setRevokeConfirm(key)}
                          className="text-red-400 hover:text-red-300 hover:bg-red-950"
                        >
                          <Trash2 className="h-4 w-4 mr-1" />
                          Revoke
                        </Button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <Table className="min-w-[900px]">
                <TableHeader>
                  <TableRow>
                    <TableHead className="sticky left-0 z-10 bg-card">Prefix</TableHead>
                    <TableHead>Name</TableHead>
                    <TableHead>Scopes</TableHead>
                    <TableHead>Created</TableHead>
                    <TableHead>Last Used</TableHead>
                    <TableHead className="sticky right-0 z-10 bg-card text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {visibleKeys.map((key) => (
                    <TableRow
                      key={key.id}
                      className={key.is_revoked ? 'opacity-50' : undefined}
                    >
                      <TableCell className="font-mono text-sm sticky left-0 z-10 bg-card">
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
                      <TableCell className="text-right sticky right-0 z-10 bg-card">
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
            )
          )}
          {filteredKeys.length > 0 && (
            <p className="pt-4 text-sm text-muted-foreground text-center">
              Showing {visibleKeys.length} of {filteredKeys.length} keys
            </p>
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
