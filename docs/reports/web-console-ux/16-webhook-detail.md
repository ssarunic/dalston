# 16 — Webhook Detail & Deliveries

**Route:** `/webhooks/:endpointId`
**Component:** `src/pages/WebhookDetail.tsx`
**Auth required:** Yes

## Purpose

Inspect a specific webhook endpoint's configuration and browse its delivery history. Allows filtering deliveries by status, retrying failed deliveries, and paginating through delivery records.

## Storyboard

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│  [← Back to Webhooks]                                        │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ 🔗 Webhook Endpoint                                    │  │
│  │                                                        │  │
│  │  URL                          Status                   │  │
│  │  https://example.com/wh/tx    🟢 Active                │  │
│  │                               Last success: 2h ago     │  │
│  │                                                        │  │
│  │  Events                       Created                  │  │
│  │  [transcription.completed]    Mar 15, 2026, 2:30 PM    │  │
│  │  [transcription.failed]                                │  │
│  │                                                        │  │
│  │  Description                                           │  │
│  │  Production notification endpoint                      │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ Delivery History      [All ▾] [Newest ▾] [20 ▾] [🔄]  │  │
│  │                                                        │  │
│  │  Event            │ Job      │Status │Att│Error│Crtd│ A│  │
│  │  ─────────────────┼──────────┼───────┼───┼─────┼────┼──│  │
│  │  tx.completed     │ a1b2c3..→│✅ succ│ 1 │  -  │ 2h │  │  │
│  │  tx.completed     │ d4e5f6..→│✅ succ│ 1 │  -  │ 5h │  │  │
│  │  tx.failed        │ g7h8i9..→│❌ fail│ 3 │Time-│ 1d │🔄│  │
│  │                   │          │       │(5)│out  │    │  │  │
│  │  tx.completed     │ j0k1l2..→│⏳ pend│ 0 │  -  │ 1d │  │  │
│  │                                                        │  │
│  │  Showing 4 deliveries         [Load more]              │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

## Sections

### 1. Back Link
`<BackButton>` linking to `/webhooks`.

### 2. Endpoint Info Card
2-column grid (`md:grid-cols-2`) showing:

| Field | Description |
|-------|-------------|
| URL | Full URL in monospace, word-breaking |
| Status | Active (green) / Inactive (gray) / Auto-disabled (orange) badge. Consecutive failure count if > 0. Last success time if available |
| Events | Secondary badges for each subscribed event type |
| Created | Full date/time string |
| Description | Shown spanning 2 columns if present |

### 3. Delivery History Card

**Header:** "Delivery History" title with filter controls and refresh button.

**Filters:**

| Filter | Options | Default |
|--------|---------|---------|
| Status | All statuses, Pending, Success, Failed | All |
| Sort | Newest first, Oldest first | Newest first |
| Page size | 20, 50, 100 | 20 |
| Refresh | RefreshCw button (spins while fetching) | — |

Note: Uses native `<select>` elements rather than shadcn Select components.

**Desktop Table (min-width 980px):**

| Column | Description |
|--------|-------------|
| Event | Badge with event type (e.g. `transcription.completed`) |
| Job | Truncated job ID linking to `/jobs/:jobId` with ExternalLink icon. "-" if no job |
| Status | Colored badge: pending (yellow/Clock), success (green/CheckCircle), failed (red/XCircle) |
| Attempts | Number + HTTP status code in parentheses if available |
| Last Error | Truncated red text (max 200px), full text on hover. "-" if none |
| Created | Relative time |
| Actions | Retry button (RefreshCw) for failed deliveries only |

Event and Actions columns are sticky.

**Mobile:** Stacked cards with event badge, status badge, 4-field grid (Job, Created, Attempts, Last Error), and retry button for failed deliveries.

### 4. Pagination Footer (`<ListLoadMoreFooter>`)
Shows "Showing X deliveries" + "Load more" button when more pages exist. Uses cursor-based infinite pagination via `useInfiniteQuery`.

## Behaviour

- Endpoint data from `useWebhooks()` — finds the matching endpoint by ID from the full list.
- Deliveries from `useWebhookDeliveries(endpointId, { status, sort, limit })` using infinite query.
- Retry failed deliveries via `useRetryDelivery`.
- Not found state: BackButton + centered AlertCircle + "Webhook endpoint not found".
- Loading state: implicit (content doesn't render until ready).
- Error state: red AlertCircle + "Failed to load deliveries".
- Empty state: Clock icon + "No deliveries yet" + hint text.
