import { useCallback, useSyncExternalStore } from 'react'

/**
 * Track whether a media query currently matches.
 * Defaults to false during SSR.
 */
export function useMediaQuery(query: string): boolean {
  const subscribe = useCallback(
    (onStoreChange: () => void) => {
      if (typeof window === 'undefined') return () => {}

      const mediaQuery = window.matchMedia(query)
      const handleChange = () => onStoreChange()

      mediaQuery.addEventListener('change', handleChange)
      return () => mediaQuery.removeEventListener('change', handleChange)
    },
    [query]
  )

  const getSnapshot = useCallback(() => {
    if (typeof window === 'undefined') return false
    return window.matchMedia(query).matches
  }, [query])

  return useSyncExternalStore(subscribe, getSnapshot, () => false)
}
