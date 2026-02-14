import { useState } from 'react'
import { Clock, Plus, Trash2, AlertCircle, Shield, Lock } from 'lucide-react'
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
import { useRetentionPolicies, useDeleteRetentionPolicy } from '@/hooks/useRetentionPolicies'
import { CreatePolicyDialog } from '@/components/CreatePolicyDialog'
import type { RetentionPolicy } from '@/api/types'

const MODE_LABELS: Record<string, { label: string; color: string }> = {
  auto_delete: { label: 'Auto Delete', color: 'bg-blue-500/10 text-blue-500 border-blue-500/20' },
  keep: { label: 'Keep Forever', color: 'bg-green-500/10 text-green-500 border-green-500/20' },
  none: { label: 'Zero Retention', color: 'bg-orange-500/10 text-orange-500 border-orange-500/20' },
}

const SCOPE_LABELS: Record<string, string> = {
  all: 'All artifacts',
  audio_only: 'Audio only',
}

function formatHours(hours: number | null): string {
  if (hours === null) return '-'
  if (hours < 24) return `${hours}h`
  const days = Math.floor(hours / 24)
  const remainingHours = hours % 24
  if (remainingHours === 0) {
    if (days >= 365) {
      const years = Math.floor(days / 365)
      return `${years}y`
    }
    return `${days}d`
  }
  return `${days}d ${remainingHours}h`
}

export function RetentionPolicies() {
  const [createDialogOpen, setCreateDialogOpen] = useState(false)
  const [deleteConfirm, setDeleteConfirm] = useState<RetentionPolicy | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)

  const { data, isLoading, error } = useRetentionPolicies()
  const deletePolicy = useDeleteRetentionPolicy()

  const handlePolicyCreated = () => {
    setCreateDialogOpen(false)
  }

  const handleDelete = async (policy: RetentionPolicy) => {
    setDeleteError(null)
    try {
      await deletePolicy.mutateAsync(policy.id)
      setDeleteConfirm(null)
    } catch (err: unknown) {
      // Map HTTP status codes to user-friendly messages
      let message = 'Failed to delete policy'
      if (err && typeof err === 'object' && 'response' in err) {
        const response = (err as { response: Response }).response
        if (response.status === 409) {
          message = 'Cannot delete policy that is currently in use by jobs'
        } else if (response.status === 400) {
          message = 'Cannot delete system policies'
        } else if (response.status === 404) {
          message = 'Policy not found'
        }
      } else if (err instanceof Error) {
        message = err.message
      }
      setDeleteError(message)
    }
  }

  const policies = data?.policies ?? []
  const systemPolicies = policies.filter((p) => p.is_system)
  const tenantPolicies = policies.filter((p) => !p.is_system)

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Retention Policies</h1>
          <p className="text-muted-foreground">Configure automatic data retention and cleanup</p>
        </div>
        <Button onClick={() => setCreateDialogOpen(true)}>
          <Plus className="h-4 w-4 mr-2" />
          Create Policy
        </Button>
      </div>

      {/* System Policies */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Lock className="h-5 w-5" />
            System Policies
          </CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-3">
              {[...Array(3)].map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : error ? (
            <div className="flex items-center gap-2 text-destructive py-4">
              <AlertCircle className="h-4 w-4" />
              <span>Failed to load policies: {error instanceof Error ? error.message : 'Unknown error'}</span>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Mode</TableHead>
                  <TableHead>Retention</TableHead>
                  <TableHead>Scope</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {systemPolicies.map((policy) => (
                  <TableRow key={policy.id}>
                    <TableCell className="font-medium">
                      <div className="flex items-center gap-2">
                        <Shield className="h-4 w-4 text-muted-foreground" />
                        {policy.name}
                      </div>
                    </TableCell>
                    <TableCell>
                      <Badge variant="outline" className={MODE_LABELS[policy.mode]?.color}>
                        {MODE_LABELS[policy.mode]?.label || policy.mode}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {formatHours(policy.hours)}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {SCOPE_LABELS[policy.scope] || policy.scope}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Tenant Policies */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Clock className="h-5 w-5" />
            Custom Policies
          </CardTitle>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-3">
              {[...Array(2)].map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : error ? (
            <div className="flex items-center gap-2 text-destructive py-4">
              <AlertCircle className="h-4 w-4" />
              <span>Failed to load policies: {error instanceof Error ? error.message : 'Unknown error'}</span>
            </div>
          ) : tenantPolicies.length === 0 ? (
            <div className="text-center py-8 text-muted-foreground">
              <Clock className="h-12 w-12 mx-auto mb-4 opacity-50" />
              <p>No custom policies</p>
              <p className="text-sm mt-1">Create a policy to define custom retention rules</p>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Mode</TableHead>
                  <TableHead>Retention</TableHead>
                  <TableHead>Scope</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {tenantPolicies.map((policy) => (
                  <TableRow key={policy.id}>
                    <TableCell className="font-medium">{policy.name}</TableCell>
                    <TableCell>
                      <Badge variant="outline" className={MODE_LABELS[policy.mode]?.color}>
                        {MODE_LABELS[policy.mode]?.label || policy.mode}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {formatHours(policy.hours)}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {SCOPE_LABELS[policy.scope] || policy.scope}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setDeleteConfirm(policy)}
                        className="text-red-400 hover:text-red-300 hover:bg-red-950"
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Create Policy Dialog */}
      <CreatePolicyDialog
        open={createDialogOpen}
        onOpenChange={setCreateDialogOpen}
        onCreated={handlePolicyCreated}
      />

      {/* Delete Confirmation Dialog */}
      <Dialog
        open={deleteConfirm !== null}
        onOpenChange={(open) => {
          if (!open) {
            setDeleteConfirm(null)
            setDeleteError(null)
          }
        }}
      >
        <Card className="w-full max-w-md mx-4">
          <CardHeader>
            <CardTitle className="text-destructive">Delete Retention Policy</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-sm text-muted-foreground">
              Are you sure you want to delete this retention policy? Jobs using this policy will keep their snapshotted retention settings.
            </p>
            {deleteConfirm && (
              <div className="bg-muted p-3 rounded-md">
                <p className="font-medium">{deleteConfirm.name}</p>
                <p className="text-sm text-muted-foreground">
                  {MODE_LABELS[deleteConfirm.mode]?.label} - {formatHours(deleteConfirm.hours)}
                </p>
              </div>
            )}
            {deleteError && (
              <div className="flex items-center gap-2 text-sm text-destructive">
                <AlertCircle className="h-4 w-4" />
                <span>{deleteError}</span>
              </div>
            )}
            <div className="flex justify-end gap-2">
              <Button
                variant="outline"
                onClick={() => {
                  setDeleteConfirm(null)
                  setDeleteError(null)
                }}
              >
                Cancel
              </Button>
              <Button
                variant="destructive"
                onClick={() => deleteConfirm && handleDelete(deleteConfirm)}
                disabled={deletePolicy.isPending}
              >
                {deletePolicy.isPending ? 'Deleting...' : 'Delete Policy'}
              </Button>
            </div>
          </CardContent>
        </Card>
      </Dialog>
    </div>
  )
}
