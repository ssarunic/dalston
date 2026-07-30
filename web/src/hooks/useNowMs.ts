import { useSyncExternalStore } from 'react'

// Wall-clock external store, quantized to whole seconds so the snapshot
// is stable within a tick (useSyncExternalStore requirement).
function subscribeEverySecond(onTick: () => void) {
  const id = setInterval(onTick, 1000)
  return () => clearInterval(id)
}
const nowSeconds = () => Math.floor(Date.now() / 1000)

/** Wall-clock in ms that ticks every second, render-pure. */
export function useNowMs(): number | null {
  return useSyncExternalStore(subscribeEverySecond, nowSeconds, nowSeconds) * 1000
}
