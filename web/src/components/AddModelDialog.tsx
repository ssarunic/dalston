import { useEffect } from 'react'
import { X } from 'lucide-react'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { HFModelInput } from '@/components/HFModelInput'
import type { HFResolveResponse } from '@/api/types'

interface AddModelDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onResolve: (modelId: string) => void
  isLoading: boolean
  result?: HFResolveResponse
  error?: Error | null
}

export function AddModelDialog({
  open,
  onOpenChange,
  onResolve,
  isLoading,
  result,
  error,
}: AddModelDialogProps) {
  // Close dialog automatically when model is successfully added
  useEffect(() => {
    if (result?.can_route) {
      onOpenChange(false)
    }
  }, [result?.can_route, onOpenChange])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between pb-2">
            <CardTitle className="text-base">Add Model from HuggingFace</CardTitle>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              onClick={() => onOpenChange(false)}
            >
              <X className="h-4 w-4" />
              <span className="sr-only">Close</span>
            </Button>
          </CardHeader>
          <CardContent>
            <HFModelInput
              onResolve={onResolve}
              isLoading={isLoading}
              result={result}
              error={error}
            />
          </CardContent>
        </Card>
      </DialogContent>
    </Dialog>
  )
}
