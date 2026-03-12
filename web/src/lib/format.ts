import type { ModelRegistryEntry } from '@/api/types'

/** Format bytes to human-readable string. */
export function formatBytes(bytes: number | null): string {
  if (bytes === null || bytes === 0) return '-'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(1024))
  return `${(bytes / Math.pow(1024, i)).toFixed(i > 0 ? 1 : 0)} ${units[i]}`
}

/** Format download progress as "downloaded / expected" string. */
export function formatDownloadProgress(model: ModelRegistryEntry): string | null {
  if (model.status !== 'downloading') return null
  const downloaded = formatBytes(model.downloaded_bytes ?? null)
  const expected = formatBytes(model.expected_total_bytes ?? null)
  if (downloaded === '-' && expected === '-') return null
  return `${downloaded} / ${expected}`
}

/** Format large numbers with K/M suffix. */
export function formatNumber(num: number | undefined): string {
  if (num === undefined) return '-'
  if (num >= 1_000_000) return `${(num / 1_000_000).toFixed(1)}M`
  if (num >= 1_000) return `${(num / 1_000).toFixed(1)}K`
  return num.toString()
}
