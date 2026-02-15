import { useState, useCallback, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'

export interface TableStateOptions<T> {
  /** Default filter values when none are in URL */
  defaultFilters?: Record<string, string>
  /** Key used to store data in the accumulated items array */
  dataKey: string
  /** Function to extract items from API response */
  getItems: (data: T) => unknown[]
  /** Function to extract cursor from API response */
  getCursor: (data: T) => string | null | undefined
  /** Function to check if there are more items */
  getHasMore: (data: T) => boolean
}

export interface TableState<TItem> {
  /** Current cursor value (from URL or undefined for first page) */
  cursor: string | undefined
  /** All accumulated items across pages */
  items: TItem[]
  /** Current filter values */
  filters: Record<string, string>
  /** Whether there are more items to load */
  hasMore: boolean
  /** Update a filter value (resets pagination) */
  setFilter: (key: string, value: string) => void
  /** Load more items using the next cursor */
  loadMore: () => void
  /** Reset all state (pagination and filters) */
  reset: () => void
  /** Process new data from API (call this in useEffect when data changes) */
  processData: (data: unknown) => void
  /** Clear accumulated items (useful before refetch) */
  clearItems: () => void
}

/**
 * Hook for managing table state with URL-synced pagination and filters.
 *
 * Stores cursor and filters in URL search params so they persist across navigation.
 * When user navigates to detail page and back, the URL params restore the table state.
 *
 * @example
 * ```tsx
 * const { cursor, items, filters, setFilter, loadMore, processData } = useTableState<Job>({
 *   defaultFilters: { status: '' },
 *   dataKey: 'jobs',
 *   getItems: (data) => data.jobs,
 *   getCursor: (data) => data.cursor,
 *   getHasMore: (data) => data.has_more,
 * })
 *
 * const { data, isFetching } = useJobs({
 *   cursor,
 *   status: filters.status || undefined,
 * })
 *
 * useEffect(() => {
 *   if (data) processData(data)
 * }, [data, processData])
 * ```
 */
export function useTableState<TItem, TData = unknown>(
  options: TableStateOptions<TData>
): TableState<TItem> {
  const { defaultFilters = {}, getItems, getCursor, getHasMore } = options
  const [searchParams, setSearchParams] = useSearchParams()
  const [items, setItems] = useState<TItem[]>([])
  const [hasMore, setHasMore] = useState(false)
  const lastCursorRef = useRef<string | undefined>(undefined)

  // Read cursor from URL (undefined means first page)
  const cursor = searchParams.get('cursor') || undefined

  // Read filters from URL, falling back to defaults
  const filters: Record<string, string> = {}
  for (const key of Object.keys(defaultFilters)) {
    filters[key] = searchParams.get(key) ?? defaultFilters[key]
  }

  // Update a filter and reset pagination
  const setFilter = useCallback(
    (key: string, value: string) => {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          if (value && value !== defaultFilters[key]) {
            next.set(key, value)
          } else {
            next.delete(key)
          }
          // Reset cursor when filter changes
          next.delete('cursor')
          return next
        },
        { replace: true }
      )
      // Clear items when filter changes
      setItems([])
      lastCursorRef.current = undefined
    },
    [setSearchParams, defaultFilters]
  )

  // Load more items by setting cursor in URL
  const loadMore = useCallback(() => {
    if (!hasMore) return
    const nextCursor = getCursor(lastDataRef.current as TData)
    if (nextCursor) {
      setSearchParams(
        (prev) => {
          const next = new URLSearchParams(prev)
          next.set('cursor', nextCursor)
          return next
        },
        { replace: true }
      )
    }
  }, [hasMore, setSearchParams, getCursor])

  // Reset all state
  const reset = useCallback(() => {
    setSearchParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        next.delete('cursor')
        for (const key of Object.keys(defaultFilters)) {
          next.delete(key)
        }
        return next
      },
      { replace: true }
    )
    setItems([])
    setHasMore(false)
    lastCursorRef.current = undefined
  }, [setSearchParams, defaultFilters])

  // Clear items without affecting URL
  const clearItems = useCallback(() => {
    setItems([])
    lastCursorRef.current = undefined
  }, [])

  // Store last data for cursor extraction
  const lastDataRef = useRef<unknown>(null)

  // Process new data from API
  const processData = useCallback(
    (data: unknown) => {
      if (!data) return
      // eslint-disable-next-line react-hooks/immutability -- refs are mutable by design
      lastDataRef.current = data
      const newItems = getItems(data as TData) as TItem[]
      const newHasMore = getHasMore(data as TData)

      setHasMore(newHasMore)

      // If cursor changed (pagination), append items
      // If no cursor (first page or filter changed), replace items
      if (cursor && cursor === lastCursorRef.current) {
        // Same cursor, data might be refetched - don't duplicate
        return
      }

      if (cursor) {
        // Loading more - append new items
        setItems((prev) => [...prev, ...newItems])
      } else {
        // First page - replace items
        setItems(newItems)
      }
      lastCursorRef.current = cursor
    },
    [cursor, getItems, getHasMore]
  )

  return {
    cursor,
    items,
    filters,
    hasMore,
    setFilter,
    loadMore,
    reset,
    processData,
    clearItems,
  }
}
