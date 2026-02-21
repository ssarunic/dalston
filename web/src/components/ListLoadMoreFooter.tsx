import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface ListLoadMoreFooterProps {
  count: number
  itemLabel: string
  hasNextPage?: boolean
  isFetchingNextPage?: boolean
  onLoadMore?: () => void
  className?: string
}

export function ListLoadMoreFooter({
  count,
  itemLabel,
  hasNextPage = false,
  isFetchingNextPage = false,
  onLoadMore,
  className,
}: ListLoadMoreFooterProps) {
  if (count <= 0) return null

  return (
    <div className={cn('flex flex-col items-center gap-3 pt-4', className)}>
      <p className="text-sm text-muted-foreground">
        Showing {count} {itemLabel}
      </p>
      {hasNextPage && onLoadMore && (
        <Button variant="outline" onClick={onLoadMore} disabled={isFetchingNextPage}>
          {isFetchingNextPage ? 'Loading...' : 'Load More'}
        </Button>
      )}
    </div>
  )
}
