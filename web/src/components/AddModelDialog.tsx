import { useEffect } from 'react'
import { X } from 'lucide-react'
import { Dialog, DialogContent } from '@/components/ui/dialog'
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
      <DialogContent className="max-w-2xl">
        <div className="bg-card rounded-lg border shadow-lg">
          <div className="flex items-center justify-between p-4 border-b">
            <h3 className="text-base font-semibold">Add Model from HuggingFace</h3>
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              onClick={() => onOpenChange(false)}
            >
              <X className="h-4 w-4" />
              <span className="sr-only">Close</span>
            </Button>
          </div>
          <div className="p-4">
            <HFModelInput
              onResolve={onResolve}
              isLoading={isLoading}
              result={result}
              error={error}
              autoFocus={open}
            />
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
