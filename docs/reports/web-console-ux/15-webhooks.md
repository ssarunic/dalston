# 15 — Webhooks

**Route:** `/webhooks`
**Component:** `src/pages/Webhooks.tsx`
**Auth required:** Yes

## Purpose

Manage webhook endpoints that receive event notifications (e.g. transcription completed, failed, cancelled). Create, activate/deactivate, rotate secrets, and delete webhook endpoints.

## Storyboard

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│  Webhooks                              [+ Create Webhook]    │
│  Event notification endpoints                                │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ 🔗 Webhook Endpoints  [All ▾] [Newest ▾] [20 ▾]       │  │
│  │                                                        │  │
│  │  URL                  │ Events          │Status│Crtd│ A│  │
│  │  ─────────────────────┼─────────────────┼──────┼────┼──│  │
│  │  https://example.co...│ tx.completed    │Active│ 5d │⏻🔄🗑│
│  │  Production webhook   │ tx.failed       │      │ago │  │  │
│  │  ─────────────────────┼─────────────────┼──────┼────┼──│  │
│  │  https://staging.ex...│ *               │Inact.│12d │⏻🔄🗑│
│  │                       │                 │      │ago │  │  │
│  │  ─────────────────────┼─────────────────┼──────┼────┼──│  │
│  │  https://monitor.ex...│ tx.failed       │Auto- │ 1m │⏻🔄🗑│
│  │  Alerting endpoint    │ tx.cancelled    │disabl│ago │  │  │
│  │                       │                 │3 fail│    │  │  │
│  │                                                        │  │
│  │  Showing 3 of 3 webhooks                               │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ 🔑 Webhook Secret                          [×]         │  │
│  │                                                        │  │
│  │  Your webhook signing secret (shown once):             │  │
│  │  ┌──────────────────────────────────────────────────┐  │  │
│  │  │ whsec_abc123def456ghi789jkl012mno345pqr678st    │  │  │
│  │  └──────────────────────────────────────────────────┘  │  │
│  │  ⚠ Copy this secret now. It will not be shown again.   │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

## Layout

**Desktop:** Full `<Table>` with 5 columns (URL, Events, Status, Created, Actions). Min-width 900px. URL and Actions columns are sticky.

**Mobile:** Stacked card layout — each webhook is a bordered card with URL, description, event badges, status/time, and action buttons.

Rows are clickable — navigate to `/webhooks/:endpointId` for delivery history.

## Elements

### Table Filters (in CardHeader)

| Filter | Options | Default |
|--------|---------|---------|
| Status | All, Active, Inactive | All |
| Sort | Newest first, Oldest first | Newest first |
| Rows | 20, 50, 100 | 20 |

### Webhook Row

| Element | Description |
|---------|-------------|
| URL | Monospace, truncated to 50 chars with tooltip for full URL |
| Description | Muted subtitle below URL (if present) |
| Event badges | Color-coded: `transcription.completed` (green), `transcription.failed` (red), `transcription.cancelled` (orange), `*` (purple). Gray fallback for unknown events |
| Status badge | Green "Active", gray "Inactive", or orange "Auto-disabled" |
| Consecutive failures | Shown below status when > 0 |
| Created | Relative time |

Inactive webhooks render at 50% opacity (desktop) or 60% opacity (mobile).

### Action Buttons (per row, stop propagation to prevent navigation)

| Action | Icon | Description |
|--------|------|-------------|
| Toggle active | ToggleRight (green) / ToggleLeft | Activate or deactivate the endpoint |
| Rotate secret | RefreshCw | Generate a new signing secret — shows secret modal |
| Delete | Trash2 (red) | Opens delete confirmation dialog |

### Create Webhook Dialog (`<CreateWebhookDialog>`)
Separate component for webhook creation form.

### Secret Modal (`<WebhookSecretModal>`)
Used for both creation and rotation. Shows the signing secret once with copy warning. `isRotation` prop differentiates the two contexts.

### Delete Confirmation Dialog

| Element | Description |
|---------|-------------|
| Title | Red "Delete Webhook" heading |
| Warning text | Explains this action is irreversible |
| Webhook preview | Shows URL + description in muted box |
| Error display | Shown if delete fails |
| Actions | Cancel + "Delete Webhook" (destructive, shows "Deleting..." while pending) |

## Behaviour

- Data from `useWebhooks(isActiveFilter)` — fetches `/console/webhooks/endpoints`.
- Client-side sorting by created date.
- Client-side limit (slice).
- "Showing X of Y webhooks" footer.
- Toggle active/inactive via `useUpdateWebhook` PATCH.
- Rotate secret via `useRotateWebhookSecret`.
- Loading state: implicit (no explicit skeleton).
- Error state: red AlertCircle + "Failed to load webhooks".
- Empty state: Webhook icon + "No webhooks found" + hint.
