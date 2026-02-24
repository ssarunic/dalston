/**
 * Format retention days for human display.
 *
 * @param retentionDays - Number of days (0=transient, -1=permanent, N=days)
 * @returns Human-readable string: "Transient", "Permanent", "1 day", "30 days"
 */
export function formatRetentionDisplay(retentionDays: number): string {
  if (retentionDays === 0) return 'Transient'
  if (retentionDays === -1) return 'Permanent'
  if (retentionDays === 1) return '1 day'
  return `${retentionDays} days`
}

/**
 * Format purge countdown for display.
 *
 * @param purgeAfter - ISO date string of when purge is scheduled
 * @returns Object with text and optional subtitle
 */
export function formatPurgeCountdown(purgeAfter: string | null | undefined): {
  text: string
  subtitle?: string
} {
  if (!purgeAfter) return { text: '-' }

  const purgeDate = new Date(purgeAfter)
  const now = new Date()
  const diffMs = purgeDate.getTime() - now.getTime()

  if (diffMs <= 0) {
    return { text: 'Pending purge' }
  }

  const diffHours = Math.floor(diffMs / (1000 * 60 * 60))
  const diffDays = Math.floor(diffHours / 24)

  if (diffDays > 0) {
    return { text: `${diffDays}d ${diffHours % 24}h`, subtitle: 'until purge' }
  }
  if (diffHours > 0) {
    return { text: `${diffHours}h`, subtitle: 'until purge' }
  }
  const diffMins = Math.floor(diffMs / (1000 * 60))
  return { text: `${diffMins}m`, subtitle: 'until purge' }
}
