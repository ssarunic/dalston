/**
 * Query timing constants for React Query hooks.
 *
 * Centralizes polling intervals, retry counts, and timeouts
 * used across the web console for API interactions.
 */

// =============================================================================
// Polling Intervals (refetchInterval)
// =============================================================================
// How often to automatically refetch data from the server

/** Standard polling for dashboard/list views (5 seconds) */
export const POLL_INTERVAL_STANDARD_MS = 5000

/** Fast polling for active/in-progress items (2 seconds) */
export const POLL_INTERVAL_ACTIVE_MS = 2000

// =============================================================================
// Retry Configuration
// =============================================================================
// How many times to retry failed requests

/** Default retry count for most queries */
export const QUERY_RETRY_COUNT = 1

/** No retries - fail immediately (for task lists, etc.) */
export const QUERY_RETRY_NONE = false

// =============================================================================
// Request Timeouts
// =============================================================================
// How long to wait before timing out a request

/** Default request timeout (30 seconds) */
export const REQUEST_TIMEOUT_MS = 30000
