# 17 — Audit Log

**Route:** `/audit`
**Component:** `src/pages/AuditLog.tsx`
**Auth required:** Yes

## Purpose

Chronological log of all auditable events in the system — job lifecycle, transcript access, key management, session activity, and data retention operations. Supports filtering, searching, and drill-down into event details.

## Storyboard

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│  Audit Log                                                   │
│  System activity and compliance trail                        │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ 📜 Events                    [🔍 Filters ▾] [🔄 Refresh]│  │
│  │                                                        │  │
│  │  ┌──────────────────────────────────────────────────┐  │  │
│  │  │ Filters                                [× Clear] │  │  │
│  │  │                                                  │  │  │
│  │  │ Resource  Action   Actor   Since  Until  Sort  Pg│  │  │
│  │  │ [All ▾]  [      ] [     ] [📅  ] [📅  ] [New▾][50]│  │  │
│  │  └──────────────────────────────────────────────────┘  │  │
│  │                                                        │  │
│  │   │ Timestamp        │ Action          │Resource │Actor│  │
│  │  ─┼──────────────────┼─────────────────┼─────────┼─────│  │
│  │  ▸│ Mar 20, 3:45 PM  │ job.completed   │job/     │sk-ab│  │
│  │   │                  │                 │a1b2c3..→│     │  │
│  │  ─┼──────────────────┼─────────────────┼─────────┼─────│  │
│  │  ▾│ Mar 20, 3:40 PM  │ transcript.     │job/     │sk-ab│  │
│  │   │                  │ accessed        │d4e5f6..→│     │  │
│  │   │ ┌────────────────────────────────────────────┐    │  │
│  │   │ │ {                                          │    │  │
│  │   │ │   "format": "srt",                        │    │  │
│  │   │ │   "ip": "192.168.1.100"                   │    │  │
│  │   │ │ }                                          │    │  │
│  │   │ └────────────────────────────────────────────┘    │  │
│  │  ─┼──────────────────┼─────────────────┼─────────┼─────│  │
│  │  ▸│ Mar 20, 3:38 PM  │ session.started │session/ │sk-ab│  │
│  │   │                  │                 │f7e2a1..→│     │  │
│  │  ─┼──────────────────┼─────────────────┼─────────┼─────│  │
│  │  ▸│ Mar 20, 3:35 PM  │ api_key.created │api_key/ │sk-ab│  │
│  │   │                  │                 │g8h9i0.. │     │  │
│  │                                                        │  │
│  │  Showing 4 events                    [Load more]       │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

## Sections

### 1. Header
Title "Audit Log" + subtitle. No action buttons in the header.

### 2. Events Card

**Card Header:** ScrollText icon + "Events" title on the left. "Filters" toggle button (shows badge when filters active) and "Refresh" button on the right.

### 3. Expandable Filter Panel

Toggled by the Filters button. 7-column grid on desktop (`md:grid-cols-7`):

| Filter | Type | Description |
|--------|------|-------------|
| Resource Type | Select | All, Job, Transcript, Audio, Session, API Key, Retention Policy |
| Action | Text input | Free-text filter (e.g. "job.completed", "deleted") |
| Actor ID | Text input | Filter by API key prefix or actor identifier |
| Since | datetime-local input | Start of date range |
| Until | datetime-local input | End of date range |
| Sort | Select | Newest first, Oldest first |
| Rows per page | Select | 25, 50, 100 |

"Clear" button appears when any filter is active. Date inputs use `ref`-based approach — values are applied on Refresh click rather than on change.

### 4. Events Table

**Desktop (min-width 860px):**

| Column | Description |
|--------|-------------|
| Expand chevron | ChevronRight/ChevronDown — only shown for events with `detail` |
| Timestamp | Full date/time string, sticky |
| Action | Color-coded badge: created (green), completed (blue), accessed (slate), exported (purple), deleted (red), purged (orange), failed (red), started (cyan), ended (slate), revoked (red), cancelled (yellow) |
| Resource | `resource_type/` prefix in muted text + truncated `resource_id` (8 chars). Linkable for `job` → `/jobs/:id` and `session` → `/realtime/sessions/:id` |
| Actor | Monospace actor ID, truncated to 16 chars |
| IP Address | IP or "-" |

Clicking a row toggles the detail expansion. Expanded row shows JSON detail in monospace `<pre>` block with muted background.

**Mobile:** Stacked cards with action badge + timestamp, resource link, actor/IP 2-column grid, and collapsible `<details>` for JSON detail.

### 5. Pagination Footer (`<ListLoadMoreFooter>`)
"Showing X events" + "Load more" button. Cursor-based infinite pagination via `useInfiniteQuery`.

## Behaviour

- Data from `useAuditEvents(filters)` — infinite query with cursor-based pagination.
- All filter state persisted in URL search params via `useSharedTableState`.
- Active filter indicator: badge on the Filters button when any non-default filter is set.
- Refresh button applies pending date range values and refetches.
- Loading state: implicit (no skeleton).
- Error state: centered red AlertCircle + error message.
- Empty state: ScrollText icon + "No events found". Additional hint shown when filters are active.
