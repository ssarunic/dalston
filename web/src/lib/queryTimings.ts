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

/**
 * Timeout for file-upload requests (30 minutes).
 *
 * Uploads are bounded by the client's uplink, not by server latency, so the
 * default 30s budget fails any non-trivial file on a slow link — a 17 MB
 * upload over a ~1.7 Mbps uplink takes ~85s and would abort mid-transfer.
 * The native ingest path accepts files up to 3 GB, so this only has to be
 * generous enough that the console isn't the limiting factor; genuinely huge
 * files should use the audio-URL path, where the server does the download.
 */
export const UPLOAD_TIMEOUT_MS = 30 * 60 * 1000
