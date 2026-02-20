import { useEffect, useRef } from 'react'
import { useLocation } from 'react-router-dom'

const SCROLL_POSITIONS_KEY = 'dalston:scroll-positions'
const MAX_ENTRIES = 50

function getScrollPositions(): Record<string, number> {
  try {
    const stored = sessionStorage.getItem(SCROLL_POSITIONS_KEY)
    return stored ? JSON.parse(stored) : {}
  } catch {
    return {}
  }
}

function setScrollPosition(key: string, position: number) {
  const positions = getScrollPositions()
  positions[key] = position

  // Limit stored entries to prevent sessionStorage bloat
  const keys = Object.keys(positions)
  if (keys.length > MAX_ENTRIES) {
    const keysToRemove = keys.slice(0, keys.length - MAX_ENTRIES)
    keysToRemove.forEach((k) => delete positions[k])
  }

  sessionStorage.setItem(SCROLL_POSITIONS_KEY, JSON.stringify(positions))
}

export function useScrollRestoration(containerRef: React.RefObject<HTMLElement | null>) {
  const location = useLocation()
  const locationKey = location.key || location.pathname
  const isRestoringRef = useRef(false)
  const prevPathnameRef = useRef(location.pathname)

  // Restore scroll position when location changes
  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const positions = getScrollPositions()
    const savedPosition = positions[locationKey]
    const pathnameChanged = prevPathnameRef.current !== location.pathname
    prevPathnameRef.current = location.pathname

    if (savedPosition !== undefined) {
      isRestoringRef.current = true
      // Use requestAnimationFrame to ensure DOM is ready
      requestAnimationFrame(() => {
        container.scrollTop = savedPosition
        // Small delay to allow for content to load
        setTimeout(() => {
          container.scrollTop = savedPosition
          isRestoringRef.current = false
        }, 50)
      })
    } else if (pathnameChanged) {
      // Only reset scroll when navigating to a different page,
      // not when just search params change (e.g., pagination cursor)
      container.scrollTop = 0
    }
  }, [locationKey, containerRef, location.pathname])

  // Save scroll position on scroll
  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    let ticking = false
    const handleScroll = () => {
      if (isRestoringRef.current) return

      if (!ticking) {
        requestAnimationFrame(() => {
          setScrollPosition(locationKey, container.scrollTop)
          ticking = false
        })
        ticking = true
      }
    }

    container.addEventListener('scroll', handleScroll, { passive: true })
    return () => container.removeEventListener('scroll', handleScroll)
  }, [locationKey, containerRef])
}
