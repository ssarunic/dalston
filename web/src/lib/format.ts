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

/** Format a millisecond duration as "1.2s" / "12s" / "1m 30s" (— when null). */
export function formatMs(ms: number | null | undefined): string {
  if (ms == null) return '—'
  if (ms < 1000) return `${Math.round(ms)}ms`
  const secs = ms / 1000
  if (secs < 60) return `${secs.toFixed(1)}s`
  const mins = Math.floor(secs / 60)
  return `${mins}m ${Math.round(secs % 60)}s`
}

/** Truncate a job id to its first 8 characters for compact display. */
export function shortJobId(jobId: string): string {
  return jobId.slice(0, 8)
}
